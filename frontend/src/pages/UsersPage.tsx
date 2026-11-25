import { Plus, Users as UsersIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { SearchInput, Select } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/Layout";
import { Pagination } from "@/components/ui/Pagination";
import { useToast } from "@/components/ui/toastContext";
import { useAuth } from "@/features/auth/authContext";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { CreateUserDialog } from "@/features/users/CreateUserDialog";
import { useSetUserActive, useUserList } from "@/features/users/hooks";
import { UserStatusBadge } from "@/features/users/UserStatusBadge";
import { useDebounced } from "@/hooks/useDebounced";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import type { User, UserSortField, UserStatusFilter } from "@/types/auth";
import { errorMessage } from "@/utils/errors";
import { formatDate, formatDateTime } from "@/utils/format";

export const PAGE_SIZE = 20;
const SEARCH_DEBOUNCE_MS = 300;

const STATUS_OPTIONS: { value: UserStatusFilter; label: string }[] = [
  { value: "all", label: "All statuses" },
  { value: "active", label: "Active" },
  { value: "inactive", label: "Inactive" },
  { value: "locked", label: "Locked" },
];

const SORT_OPTIONS: { value: string; label: string }[] = [
  { value: "created_at:desc", label: "Newest first" },
  { value: "created_at:asc", label: "Oldest first" },
  { value: "full_name:asc", label: "Name A–Z" },
  { value: "email:asc", label: "Email A–Z" },
  { value: "last_login_at:desc", label: "Recent sign-in" },
];

export function UsersPage() {
  useDocumentTitle("Users");
  const { user: currentUser } = useAuth();
  const { hasPermission } = usePermissions();
  const canManage = hasPermission("users:manage");
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const page = Math.max(0, Number(params.get("page") ?? "0") || 0);
  const status = (params.get("status") as UserStatusFilter | null) ?? "all";
  const sortValue = params.get("sort") ?? "created_at:desc";
  const [sort, order] = sortValue.split(":") as [UserSortField, "asc" | "desc"];
  const search = params.get("q") ?? "";
  const [searchInput, setSearchInput] = useState(search);
  const debouncedSearch = useDebounced(searchInput.trim(), SEARCH_DEBOUNCE_MS);

  const updateParams = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === "") next.delete(key);
      else next.set(key, value);
    }
    setParams(next, { replace: true });
  };

  // The URL is the source of truth: a new search and the reset to page 1 land together.
  useEffect(() => {
    if (debouncedSearch !== search) updateParams({ q: debouncedSearch || null, page: null });
    // Reacts to the debounced input only.
  }, [debouncedSearch]); // eslint-disable-line react-hooks/exhaustive-deps

  const list = useUserList({ offset: page * PAGE_SIZE, limit: PAGE_SIZE, search: search || undefined, status, sort, order });
  const setActive = useSetUserActive();
  const [creating, setCreating] = useState(false);
  const [toDeactivate, setToDeactivate] = useState<User | null>(null);
  const toast = useToast();

  const total = list.data?.total ?? 0;

  const columns: DataTableColumn<User>[] = [
    {
      id: "name",
      header: "Name",
      cell: (item) => (
        <div className="flex min-w-0 items-center gap-3">
          <Avatar name={item.full_name} size="sm" />
          <div className="min-w-0">
            <Link to={`/admin/users/${item.id}`} className="font-medium text-fg hover:underline">
              {item.full_name}
            </Link>
            {item.is_superuser && <span className="ml-2 text-xs text-fg-muted">Administrator</span>}
          </div>
        </div>
      ),
    },
    { id: "email", header: "Email", cell: (item) => item.email, hideBelow: "md" },
    { id: "status", header: "Status", cell: (item) => <UserStatusBadge user={item} /> },
    { id: "last-login", header: "Last sign-in", cell: (item) => formatDateTime(item.last_login_at), hideBelow: "lg" },
    { id: "created", header: "Created", cell: (item) => formatDate(item.created_at), hideBelow: "lg" },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      cell: (item) => {
        const isSelf = item.id === currentUser?.id;
        const protectedAccount = isSelf || item.is_superuser;
        return (
          <div className="flex justify-end gap-1">
            <Button variant="ghost" size="sm" className="hidden sm:inline-flex" onClick={() => navigate(`/admin/users/${item.id}`)} aria-label={`View ${item.full_name}`}>
              View
            </Button>
            {canManage && !protectedAccount && item.is_active && (
              <Button variant="ghost" size="sm" onClick={() => setToDeactivate(item)} aria-label={`Deactivate ${item.full_name}`}>
                Deactivate
              </Button>
            )}
            {canManage && !item.is_active && (
              <Button
                variant="ghost"
                size="sm"
                aria-label={`Activate ${item.full_name}`}
                loading={setActive.isPending && setActive.variables?.id === item.id}
                onClick={() => {
                  setActive.mutate(
                    { id: item.id, active: true },
                    {
                      onSuccess: () => toast.notify({ title: "User activated", description: `${item.full_name} can sign in again.` }),
                      onError: (error) => toast.notify({ tone: "error", title: "Could not activate the user", description: errorMessage(error) }),
                    },
                  );
                }}
              >
                Activate
              </Button>
            )}
          </div>
        );
      },
    },
  ];

  const filtered = Boolean(search) || status !== "all";

  return (
    <div className="space-y-section">
      <PageHeader
        title="Users"
        description="People who can sign in to this OpsPilot AI installation."
        actions={
          canManage && (
            <Button onClick={() => setCreating(true)}>
              <Plus aria-hidden="true" className="h-4 w-4" />
              Create user
            </Button>
          )
        }
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap">
        <SearchInput
          label="Search users"
          placeholder="Search by name, email or username"
          value={searchInput}
          onValueChange={setSearchInput}
          className="min-w-0 sm:min-w-64 sm:flex-1"
        />
        <Select
          aria-label="Filter by status"
          value={status}
          onChange={(event) => updateParams({ status: event.target.value === "all" ? null : event.target.value, page: null })}
          className="sm:w-44"
        >
          {STATUS_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Sort users"
          value={sortValue}
          onChange={(event) => updateParams({ sort: event.target.value === "created_at:desc" ? null : event.target.value, page: null })}
          className="sm:w-44"
        >
          {SORT_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
      </div>

      <DataTable
        caption="Users"
        columns={columns}
        rows={list.data?.items}
        getRowId={(item) => item.id}
        loading={list.isPending}
        loadingLabel="Loading users"
        error={list.isError ? errorMessage(list.error, "Users could not be loaded.") : null}
        onRetry={() => void list.refetch()}
        retrying={list.isFetching}
        empty={{
          icon: UsersIcon,
          title: "No users found",
          description: filtered ? "Try a different search or filter." : "Create the first user to get started.",
        }}
        footer={
          list.data && (
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={total}
              itemLabel="Users"
              onPage={(next) => updateParams({ page: next ? String(next) : null })}
            />
          )
        }
      />

      <CreateUserDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(created) => {
          setCreating(false);
          toast.notify({ title: "User created", description: `${created.full_name} can now sign in.` });
          navigate(`/admin/users/${created.id}`);
        }}
      />

      <ConfirmDialog
        open={toDeactivate !== null}
        title="Deactivate user?"
        description={
          toDeactivate && (
            <>
              <strong className="font-medium text-fg">{toDeactivate.full_name}</strong> will be signed out
              everywhere and will not be able to sign in until the account is activated again.
            </>
          )
        }
        confirmLabel="Deactivate"
        pending={setActive.isPending}
        error={setActive.isError ? errorMessage(setActive.error) : null}
        onCancel={() => {
          setToDeactivate(null);
          setActive.reset();
        }}
        onConfirm={() => {
          if (!toDeactivate) return;
          const name = toDeactivate.full_name;
          setActive.mutate(
            { id: toDeactivate.id, active: false },
            {
              onSuccess: () => {
                setToDeactivate(null);
                toast.notify({ title: "User deactivated", description: `${name} has been signed out everywhere.` });
              },
            },
          );
        }}
      />
    </div>
  );
}
