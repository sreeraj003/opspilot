import { ChevronLeft, ChevronRight, Plus, Search, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { useAuth } from "@/features/auth/authContext";
import { CreateUserDialog } from "@/features/users/CreateUserDialog";
import { useSetUserActive, useUserList } from "@/features/users/hooks";
import { UserStatusBadge } from "@/features/users/UserStatusBadge";
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

const SELECT_CLASSES =
  "rounded-lg border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 shadow-sm focus:border-stone-500 focus:outline-none focus:ring-2 focus:ring-stone-900/10";

function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

export function UsersPage() {
  useDocumentTitle("Users");
  const { user: currentUser, hasPermission } = useAuth();
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
  const [actionError, setActionError] = useState<string | null>(null);

  const total = list.data?.total ?? 0;
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-stone-900">Users</h1>
          <p className="mt-1 text-sm text-stone-500">People who can sign in to this OpsPilot AI installation.</p>
        </div>
        {canManage && (
          <Button onClick={() => setCreating(true)}>
            <Plus aria-hidden="true" className="h-4 w-4" />
            Create user
          </Button>
        )}
      </div>

      <div className="flex flex-wrap gap-3">
        <div className="relative min-w-56 flex-1">
          <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-stone-400" />
          <input
            type="search"
            aria-label="Search users"
            placeholder="Search by name, email or username"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            className="w-full rounded-lg border border-stone-200 bg-white py-2 pl-9 pr-3 text-sm shadow-sm focus:border-stone-500 focus:outline-none focus:ring-2 focus:ring-stone-900/10"
          />
        </div>
        <select
          aria-label="Filter by status"
          value={status}
          onChange={(event) => updateParams({ status: event.target.value === "all" ? null : event.target.value, page: null })}
          className={SELECT_CLASSES}
        >
          {STATUS_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <select
          aria-label="Sort users"
          value={sortValue}
          onChange={(event) => updateParams({ sort: event.target.value === "created_at:desc" ? null : event.target.value, page: null })}
          className={SELECT_CLASSES}
        >
          {SORT_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      {actionError && <Alert tone="error">{actionError}</Alert>}

      <div className="overflow-hidden rounded-2xl border border-stone-200 bg-white">
        {list.isPending ? (
          <Spinner label="Loading users" />
        ) : list.isError ? (
          <div className="p-6">
            <Alert tone="error">
              {errorMessage(list.error, "Users could not be loaded.")}{" "}
              <button type="button" className="font-medium underline" onClick={() => void list.refetch()}>
                Try again
              </button>
            </Alert>
          </div>
        ) : list.data.items.length === 0 ? (
          <div className="flex flex-col items-center px-6 py-14 text-center">
            <Users aria-hidden="true" className="h-8 w-8 text-stone-300" strokeWidth={1.5} />
            <p className="mt-3 text-sm font-medium text-stone-900">No users found</p>
            <p className="mt-1 text-sm text-stone-500">
              {search || status !== "all" ? "Try a different search or filter." : "Create the first user to get started."}
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-stone-200 text-sm">
              <thead className="bg-stone-50 text-left text-xs font-medium uppercase tracking-wide text-stone-500">
                <tr>
                  <th scope="col" className="px-4 py-3">Name</th>
                  <th scope="col" className="px-4 py-3">Email</th>
                  <th scope="col" className="px-4 py-3">Status</th>
                  <th scope="col" className="px-4 py-3">Last sign-in</th>
                  <th scope="col" className="px-4 py-3">Created</th>
                  <th scope="col" className="px-4 py-3 text-right">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100">
                {list.data.items.map((item) => {
                  const isSelf = item.id === currentUser?.id;
                  const protectedAccount = isSelf || item.is_superuser;
                  return (
                    <tr key={item.id} className="hover:bg-stone-50/60">
                      <td className="px-4 py-3">
                        <Link to={`/admin/users/${item.id}`} className="font-medium text-stone-900 hover:underline">
                          {item.full_name}
                        </Link>
                        {item.is_superuser && <span className="ml-2 text-xs text-stone-500">Administrator</span>}
                      </td>
                      <td className="px-4 py-3 text-stone-600">{item.email}</td>
                      <td className="px-4 py-3">
                        <UserStatusBadge user={item} />
                      </td>
                      <td className="px-4 py-3 text-stone-600">{formatDateTime(item.last_login_at)}</td>
                      <td className="px-4 py-3 text-stone-600">{formatDate(item.created_at)}</td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex justify-end gap-1">
                          <Button variant="ghost" onClick={() => navigate(`/admin/users/${item.id}`)} aria-label={`View ${item.full_name}`}>
                            View
                          </Button>
                          {canManage && !protectedAccount && item.is_active && (
                            <Button variant="ghost" onClick={() => setToDeactivate(item)} aria-label={`Deactivate ${item.full_name}`}>
                              Deactivate
                            </Button>
                          )}
                          {canManage && !item.is_active && (
                            <Button
                              variant="ghost"
                              aria-label={`Activate ${item.full_name}`}
                              loading={setActive.isPending && setActive.variables?.id === item.id}
                              onClick={() => {
                                setActionError(null);
                                setActive.mutate(
                                  { id: item.id, active: true },
                                  { onError: (error) => setActionError(errorMessage(error)) },
                                );
                              }}
                            >
                              Activate
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {list.data && total > 0 && (
        <nav aria-label="Pagination" className="flex items-center justify-between text-sm text-stone-600">
          <p>
            Showing {page * PAGE_SIZE + 1}–{Math.min(total, (page + 1) * PAGE_SIZE)} of {total}
          </p>
          <div className="flex gap-2">
            <Button variant="secondary" disabled={page === 0} onClick={() => updateParams({ page: page - 1 ? String(page - 1) : null })}>
              <ChevronLeft aria-hidden="true" className="h-4 w-4" />
              Previous
            </Button>
            <Button variant="secondary" disabled={page >= lastPage} onClick={() => updateParams({ page: String(page + 1) })}>
              Next
              <ChevronRight aria-hidden="true" className="h-4 w-4" />
            </Button>
          </div>
        </nav>
      )}

      <CreateUserDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(created) => {
          setCreating(false);
          navigate(`/admin/users/${created.id}`);
        }}
      />

      <ConfirmDialog
        open={toDeactivate !== null}
        title="Deactivate user?"
        description={
          toDeactivate && (
            <>
              <strong className="font-medium text-stone-900">{toDeactivate.full_name}</strong> will be signed out
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
          setActive.mutate({ id: toDeactivate.id, active: false }, { onSuccess: () => setToDeactivate(null) });
        }}
      />
    </div>
  );
}
