import { ArrowLeft } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { PageHeader } from "@/components/ui/Layout";
import { Spinner } from "@/components/ui/Spinner";
import { Tabs } from "@/components/ui/Tabs";
import { useToast } from "@/components/ui/toastContext";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { ActiveBadge } from "@/features/rbac/components";
import { DatabaseTypeLabel, SchemaFormDialog } from "@/features/schemas/components";
import { HistoryTab } from "@/features/schemas/HistoryTab";
import { useDeleteSchema, useSchema } from "@/features/schemas/hooks";
import { ImportExportTab } from "@/features/schemas/ImportExportTab";
import { OverviewTab } from "@/features/schemas/OverviewTab";
import { RelationshipsTab } from "@/features/schemas/RelationshipsTab";
import { TablesTab } from "@/features/schemas/TablesTab";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { ApiError } from "@/services/apiClient";
import { errorMessage } from "@/utils/errors";

export function SchemaDetailPage() {
  const { schemaId = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const { hasPermission } = usePermissions();
  const detail = useSchema(schemaId);
  const remove = useDeleteSchema();
  const [tab, setTab] = useState("overview");
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  useDocumentTitle(detail.data?.name ?? "Schema");

  const back = (
    <Link to="/admin/schemas" className="inline-flex items-center gap-1.5 rounded-xs text-sm text-fg-muted hover:text-fg">
      <ArrowLeft aria-hidden="true" className="h-4 w-4" /> All schemas
    </Link>
  );

  if (detail.isPending) return <Spinner label="Loading schema" />;
  if (detail.isError) {
    const missing = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div className="space-y-4">
        {back}
        <Alert tone="error">{missing ? "This schema does not exist or was deleted." : errorMessage(detail.error)}</Alert>
      </div>
    );
  }
  const schema = detail.data;
  const canImportOrExport = hasPermission("schema.import") || hasPermission("schema.export");

  const items = [
    {
      id: "overview",
      label: "Overview",
      content: <OverviewTab schema={schema} />,
    },
    {
      id: "tables",
      label: "Tables",
      content: <TablesTab schemaId={schema.id} />,
    },
    {
      id: "relationships",
      label: "Relationships",
      content: <RelationshipsTab schemaId={schema.id} />,
    },
    ...(canImportOrExport
      ? [
          {
            id: "import-export",
            label: "Import / Export",
            content: <ImportExportTab schema={schema} />,
          },
        ]
      : []),
    {
      id: "history",
      label: "Schema History",
      content: <HistoryTab schemaId={schema.id} />,
    },
  ];

  return (
    <div className="space-y-section">
      <PageHeader
        eyebrow={back}
        title={schema.name}
        description={schema.description ?? <DatabaseTypeLabel type={schema.database_type} dialect={schema.dialect} />}
        meta={
          <>
            <ActiveBadge active={schema.is_active} />
            <span className="font-mono text-xs text-fg-muted">v{schema.schema_version}</span>
          </>
        }
        actions={
          <>
            {hasPermission("schema.update") && (
              <Button variant="secondary" onClick={() => setEditing(true)}>
                Edit
              </Button>
            )}
            {hasPermission("schema.delete") && (
              <Button variant="danger-ghost" onClick={() => setConfirmDelete(true)}>
                Delete
              </Button>
            )}
          </>
        }
      />

      <Tabs label="Schema sections" items={items} value={items.some((i) => i.id === tab) ? tab : "overview"} onValueChange={setTab} />

      <SchemaFormDialog open={editing} schema={schema} onClose={() => setEditing(false)} onSaved={() => setEditing(false)} />
      <ConfirmDialog
        open={confirmDelete}
        title="Delete schema?"
        description={
          <>
            <strong className="font-medium text-fg">{schema.name}</strong> and all its tables, columns and relationships are removed from
            the registry. The change history is kept.
          </>
        }
        confirmLabel="Delete schema"
        pending={remove.isPending}
        error={remove.isError ? errorMessage(remove.error) : null}
        onCancel={() => {
          setConfirmDelete(false);
          remove.reset();
        }}
        onConfirm={() =>
          remove.mutate(schema.id, {
            onSuccess: () => {
              toast.notify({
                title: "Schema deleted",
                description: schema.name,
              });
              void navigate("/admin/schemas");
            },
          })
        }
      />
    </div>
  );
}
