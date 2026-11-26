import { Cable, Plus } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { Button } from "@/components/ui/Button";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { Select } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/Layout";
import { useToast } from "@/components/ui/toastContext";
import { ConnectorFormDialog, TestStatusBadge } from "@/features/connectors/components";
import { useConnectorList } from "@/features/connectors/hooks";
import { AUTH_LABELS } from "@/features/connectors/labels";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { ActiveBadge, Pagination, SearchInput } from "@/features/rbac/components";
import { useDebounced } from "@/hooks/useDebounced";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import type { AuthenticationType, Connector, ConnectorStatusFilter, TestStatus } from "@/types/connector";
import { errorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";

export const CONNECTORS_PAGE_SIZE = 20;

export function ConnectorsPage() {
  useDocumentTitle("Connectors");
  const navigate = useNavigate();
  const toast = useToast();
  const { hasPermission } = usePermissions();
  const canCreate = hasPermission("connector.create");
  const [searchInput, setSearchInput] = useState("");
  const search = useDebounced(searchInput.trim());
  const [status, setStatus] = useState<ConnectorStatusFilter>("all");
  const [authType, setAuthType] = useState<AuthenticationType | "">("");
  const [testStatus, setTestStatus] = useState<TestStatus | "">("");
  const [page, setPage] = useState(0);
  const [creating, setCreating] = useState(false);
  const list = useConnectorList({
    offset: page * CONNECTORS_PAGE_SIZE,
    limit: CONNECTORS_PAGE_SIZE,
    search: search || undefined,
    status,
    authentication_type: authType,
    last_test_status: testStatus,
    sort: "name",
    order: "asc",
  });
  const filtered = Boolean(search) || status !== "all" || authType !== "" || testStatus !== "";
  const resetPage = () => setPage(0);

  const columns: DataTableColumn<Connector>[] = [
    {
      id: "connector",
      header: "Connector",
      cell: (connector) => (
        <div className="min-w-0">
          <Link to={`/admin/connectors/${connector.id}`} className="font-medium text-fg hover:underline">
            {connector.name}
          </Link>
          {connector.description && <p className="mt-0.5 line-clamp-2 max-w-md text-xs text-fg-muted">{connector.description}</p>}
        </div>
      ),
    },
    {
      id: "schema",
      header: "Schema",
      hideBelow: "md",
      cell: (connector) =>
        connector.schema_source_available ? (
          connector.schema_source_name
        ) : (
          <span className="text-danger-text">Schema deleted</span>
        ),
    },
    {
      id: "host",
      header: "Endpoint host",
      hideBelow: "sm",
      cell: (connector) => <span className="font-mono text-xs">{connector.endpoint_host}</span>,
    },
    { id: "auth", header: "Authentication", hideBelow: "lg", cell: (connector) => AUTH_LABELS[connector.authentication_type] },
    { id: "status", header: "Status", cell: (connector) => <ActiveBadge active={connector.is_active} /> },
    { id: "test", header: "Last test", hideBelow: "sm", cell: (connector) => <TestStatusBadge status={connector.last_test_status} /> },
    {
      id: "tested",
      header: "Last tested",
      hideBelow: "lg",
      cell: (connector) => <span className="whitespace-nowrap">{formatDateTime(connector.last_tested_at)}</span>,
    },
  ];

  return (
    <div className="space-y-section">
      <PageHeader
        title="Connectors"
        description="Your company's GET SQL endpoints. OpsPilot sends approved SQL to them and never connects to your databases directly."
        actions={
          canCreate && (
            <Button onClick={() => setCreating(true)}>
              <Plus aria-hidden="true" className="h-4 w-4" />
              New connector
            </Button>
          )
        }
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap">
        <SearchInput
          label="Search connectors"
          placeholder="Search by name, description or URL"
          value={searchInput}
          onChange={(value) => {
            setSearchInput(value);
            resetPage();
          }}
        />
        <Select
          aria-label="Filter by status"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value as ConnectorStatusFilter);
            resetPage();
          }}
          className="sm:w-40"
        >
          <option value="all">All statuses</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </Select>
        <Select
          aria-label="Filter by authentication"
          value={authType}
          onChange={(event) => {
            setAuthType(event.target.value as AuthenticationType | "");
            resetPage();
          }}
          className="sm:w-48"
        >
          <option value="">All authentication</option>
          {Object.entries(AUTH_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Filter by last test"
          value={testStatus}
          onChange={(event) => {
            setTestStatus(event.target.value as TestStatus | "");
            resetPage();
          }}
          className="sm:w-44"
        >
          <option value="">Any test result</option>
          <option value="passed">Test passed</option>
          <option value="failed">Test failed</option>
          <option value="untested">Not tested</option>
        </Select>
      </div>

      <DataTable
        caption="Connectors"
        columns={columns}
        rows={list.data?.items}
        getRowId={(connector) => connector.id}
        loading={list.isPending}
        loadingLabel="Loading connectors"
        error={list.isError ? errorMessage(list.error, "Connectors could not be loaded.") : null}
        onRetry={() => void list.refetch()}
        retrying={list.isFetching}
        empty={{
          icon: Cable,
          title: filtered ? "No connectors match" : "No connectors yet",
          description: filtered
            ? "Try a different search or filter."
            : canCreate
              ? "Create a connector for your company's GET SQL endpoint, test it, then activate it."
              : "Ask an administrator to configure your company's API connector.",
        }}
        footer={
          list.data && (
            <Pagination page={page} pageSize={CONNECTORS_PAGE_SIZE} total={list.data.total} onPage={setPage} itemLabel="Connectors" />
          )
        }
      />

      <ConnectorFormDialog
        open={creating}
        onClose={() => setCreating(false)}
        onSaved={(connector) => {
          setCreating(false);
          toast.notify({ title: "Connector created", description: `${connector.name}. Test it, then activate it.` });
          navigate(`/admin/connectors/${connector.id}`);
        }}
      />
    </div>
  );
}
