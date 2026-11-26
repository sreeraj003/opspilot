import { ArrowLeft, PlugZap } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { PageHeader } from "@/components/ui/Layout";
import { SkeletonText } from "@/components/ui/Skeleton";
import { Spinner } from "@/components/ui/Spinner";
import { StatusIndicator, type StatusTone } from "@/components/ui/StatusIndicator";
import { useToast } from "@/components/ui/toastContext";
import { ConnectorFormDialog, GetQueryWarning, TestResultPanel, TestStatusBadge } from "@/features/connectors/components";
import {
  useConnector,
  useConnectorAudit,
  useConnectorHealth,
  useDeleteConnector,
  useSetConnectorStatus,
  useTestConnector,
} from "@/features/connectors/hooks";
import { AUTH_LABELS, errorCategoryLabel, formatBytes } from "@/features/connectors/labels";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { ActiveBadge, Pagination } from "@/features/rbac/components";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { ApiError } from "@/services/apiClient";
import type { Connector, ConnectorAuditEvent, ConnectorHealth } from "@/types/connector";
import { errorMessage } from "@/utils/errors";
import { formatDateTime, humanize } from "@/utils/format";

const AUDIT_PAGE_SIZE = 10;

const HEALTH: Record<ConnectorHealth["status"], { tone: StatusTone; label: string; text: string }> = {
  ready: { tone: "success", label: "Ready", text: "Active, and its last connection test passed." },
  inactive: { tone: "neutral", label: "Inactive", text: "Not used for queries until it is activated." },
  untested: { tone: "warning", label: "Not tested", text: "Active, but it has not been tested since its configuration changed." },
  failing: { tone: "danger", label: "Failing", text: "Its last connection test failed." },
  misconfigured: { tone: "danger", label: "Needs attention", text: "Its configuration cannot be used as it is." },
};

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-4">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="min-w-0 break-words text-fg sm:text-right">{children}</dd>
    </div>
  );
}

function ConfigurationCard({ connector }: { connector: Connector }) {
  return (
    <Card title="Configuration">
      <dl className="space-y-2 text-sm">
        <Row label="Schema">
          {connector.schema_source_available ? (
            <Link to={`/admin/schemas/${connector.schema_source_id}`} className="font-medium hover:underline">
              {connector.schema_source_name}
            </Link>
          ) : (
            <span className="text-danger-text">Deleted</span>
          )}
        </Row>
        <Row label="Endpoint host">
          <span className="font-mono">{connector.endpoint_host}</span>
        </Row>
        <Row label="Request">
          <span className="font-mono text-xs">
            GET {connector.endpoint_path}?{connector.sql_parameter_name}=…
          </span>
        </Row>
        {connector.health_check_path && (
          <Row label="Health check">
            <span className="font-mono text-xs">GET {connector.health_check_path}</span>
          </Row>
        )}
        <Row label="Authentication">
          {AUTH_LABELS[connector.authentication_type]}
          {connector.auth_header_name && <span className="ml-1 font-mono text-xs text-fg-muted">({connector.auth_header_name})</span>}
        </Row>
        {connector.authentication_type !== "none" && (
          <Row label="Credential">{connector.has_credentials ? <Badge>Stored, hidden</Badge> : <Badge tone="danger">Missing</Badge>}</Row>
        )}
        <Row label="Custom headers">
          {connector.custom_headers.length === 0 ? (
            <span className="text-fg-muted">None</span>
          ) : (
            <span className="flex flex-wrap justify-end gap-1">
              {connector.custom_headers.map((header) => (
                <Badge key={header.name} mono title={header.secret ? "Secret value, hidden" : undefined}>
                  {header.name}: {header.secret ? "hidden" : header.value}
                </Badge>
              ))}
            </span>
          )}
        </Row>
        <Row label="Response format">{connector.response_contract.format === "envelope" ? "Envelope" : "Raw JSON array"}</Row>
        <Row label="Timeouts">
          {connector.timeout_seconds} s overall, {connector.connect_timeout_seconds} s connect, {connector.read_timeout_seconds} s read
        </Row>
        <Row label="Limits">
          {formatBytes(connector.max_response_size_bytes)} response, {connector.max_sql_length} SQL characters
        </Row>
        <Row label="Updated">{formatDateTime(connector.updated_at)}</Row>
      </dl>
    </Card>
  );
}

function StatusCard({ connector }: { connector: Connector }) {
  const health = useConnectorHealth(connector.id);
  return (
    <Card title="Connection status">
      {health.isPending ? (
        <SkeletonText lines={3} />
      ) : health.isError ? (
        <Alert tone="error">{errorMessage(health.error, "The connection status could not be loaded.")}</Alert>
      ) : (
        <div className="space-y-3 text-sm">
          <div className="flex items-center gap-2">
            <StatusIndicator tone={HEALTH[health.data.status].tone} label={HEALTH[health.data.status].label} />
            <span className="text-fg-muted">{HEALTH[health.data.status].text}</span>
          </div>
          {!health.data.destination_allowed && <Alert tone="error">{health.data.destination_problem}</Alert>}
          {!health.data.credentials_available && (
            <Alert tone="error">The stored credentials cannot be decrypted on this server. Check the encryption key or re-enter them.</Alert>
          )}
          {!health.data.schema_source_available && <Alert tone="error">The associated schema was deleted.</Alert>}
          <dl className="space-y-2">
            <Row label="Last test">
              <TestStatusBadge status={connector.last_test_status} />
            </Row>
            <Row label="Last tested">{formatDateTime(connector.last_tested_at)}</Row>
            {connector.last_test_status === "failed" && (
              <Row label="Last error">
                <span className="font-medium">{errorCategoryLabel(connector.last_error_category)}</span>
                {connector.last_error_summary && <span className="block text-fg-muted">{connector.last_error_summary}</span>}
              </Row>
            )}
          </dl>
        </div>
      )}
    </Card>
  );
}

function ActivityCard({ connectorId }: { connectorId: string }) {
  const [page, setPage] = useState(0);
  const audit = useConnectorAudit(connectorId, { offset: page * AUDIT_PAGE_SIZE, limit: AUDIT_PAGE_SIZE });
  const columns: DataTableColumn<ConnectorAuditEvent>[] = [
    { id: "operation", header: "Operation", cell: (event) => humanize(event.operation_type) },
    {
      id: "status",
      header: "Result",
      cell: (event) => (
        <span>
          <StatusIndicator
            variant="dot"
            tone={event.status === "success" ? "success" : event.status === "rejected" ? "warning" : "danger"}
            label={humanize(event.status)}
          />
          {event.error_category && <span className="block text-xs text-fg-muted">{errorCategoryLabel(event.error_category)}</span>}
        </span>
      ),
    },
    {
      id: "duration",
      header: "Duration",
      hideBelow: "md",
      align: "right",
      cell: (event) => (event.duration_ms === null ? "—" : `${event.duration_ms} ms`),
    },
    { id: "by", header: "By", hideBelow: "sm", cell: (event) => event.requesting_user_name ?? <span className="text-fg-muted">System</span> },
    { id: "when", header: "When", cell: (event) => <span className="whitespace-nowrap">{formatDateTime(event.created_at)}</span> },
  ];
  return (
    <Card title="Activity" description="Audit records for this connector. They never contain credentials or SQL text." padding="none">
      <DataTable
        caption="Connector activity, newest first"
        columns={columns}
        rows={audit.data?.items}
        getRowId={(event) => event.id}
        loading={audit.isPending}
        loadingLabel="Loading activity"
        error={audit.isError ? errorMessage(audit.error, "Activity could not be loaded.") : null}
        onRetry={() => void audit.refetch()}
        retrying={audit.isFetching}
        empty={{ title: "No activity yet" }}
        footer={
          audit.data && <Pagination page={page} pageSize={AUDIT_PAGE_SIZE} total={audit.data.total} onPage={setPage} itemLabel="Records" />
        }
      />
    </Card>
  );
}

export function ConnectorDetailPage() {
  const { connectorId = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const { hasPermission } = usePermissions();
  const detail = useConnector(connectorId);
  const test = useTestConnector();
  const status = useSetConnectorStatus();
  const remove = useDeleteConnector();
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  useDocumentTitle(detail.data?.name ?? "Connector");

  const back = (
    <Link to="/admin/connectors" className="inline-flex items-center gap-1.5 rounded-xs text-sm text-fg-muted hover:text-fg">
      <ArrowLeft aria-hidden="true" className="h-4 w-4" /> All connectors
    </Link>
  );

  if (detail.isPending) return <Spinner label="Loading connector" />;
  if (detail.isError) {
    const missing = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div className="space-y-4">
        {back}
        <Alert tone="error">{missing ? "This connector does not exist or was deleted." : errorMessage(detail.error)}</Alert>
      </div>
    );
  }
  const connector = detail.data;
  const result = test.data?.connector_id === connector.id ? test.data : null;

  return (
    <div className="space-y-section">
      <PageHeader
        eyebrow={back}
        title={connector.name}
        description={connector.description ?? <span className="font-mono">{connector.endpoint_host}</span>}
        meta={
          <>
            <ActiveBadge active={connector.is_active} />
            <TestStatusBadge status={connector.last_test_status} />
          </>
        }
        actions={
          <>
            {hasPermission("connector.test") && (
              <Button onClick={() => test.mutate(connector.id)} loading={test.isPending}>
                <PlugZap aria-hidden="true" className="h-4 w-4" />
                Test connection
              </Button>
            )}
            {hasPermission("connector.activate") && (
              <Button
                variant="secondary"
                loading={status.isPending}
                onClick={() =>
                  status.mutate(
                    { id: connector.id, active: !connector.is_active },
                    {
                      onSuccess: (saved) =>
                        toast.notify({ title: saved.is_active ? "Connector activated" : "Connector deactivated", description: saved.name }),
                    },
                  )
                }
              >
                {connector.is_active ? "Deactivate" : "Activate"}
              </Button>
            )}
            {hasPermission("connector.update") && (
              <Button variant="secondary" onClick={() => setEditing(true)}>
                Edit
              </Button>
            )}
            {hasPermission("connector.delete") && (
              <Button variant="danger-ghost" onClick={() => setConfirmDelete(true)}>
                Delete
              </Button>
            )}
          </>
        }
      />

      {status.isError && <Alert tone="error">{errorMessage(status.error, "The status could not be changed.")}</Alert>}
      {test.isError && <Alert tone="error">{errorMessage(test.error, "The connection test could not be run.")}</Alert>}
      {result && (
        <Card title="Last test result">
          <TestResultPanel result={result} />
        </Card>
      )}

      <div className="grid gap-section lg:grid-cols-2">
        <ConfigurationCard connector={connector} />
        <StatusCard connector={connector} />
      </div>

      <GetQueryWarning />
      <ActivityCard connectorId={connector.id} />

      <ConnectorFormDialog
        open={editing}
        connector={connector}
        onClose={() => setEditing(false)}
        onSaved={(saved) => {
          setEditing(false);
          toast.notify({ title: "Connector updated", description: saved.name });
        }}
      />
      <ConfirmDialog
        open={confirmDelete}
        title="Delete connector?"
        description={
          <>
            <strong className="font-medium text-fg">{connector.name}</strong> is removed and its stored credentials are erased. OpsPilot
            can no longer query this endpoint. Audit records are kept.
          </>
        }
        confirmLabel="Delete connector"
        pending={remove.isPending}
        error={remove.isError ? errorMessage(remove.error) : null}
        onCancel={() => {
          setConfirmDelete(false);
          remove.reset();
        }}
        onConfirm={() =>
          remove.mutate(connector.id, {
            onSuccess: () => {
              toast.notify({ title: "Connector deleted", description: connector.name });
              navigate("/admin/connectors");
            },
          })
        }
      />
    </div>
  );
}
