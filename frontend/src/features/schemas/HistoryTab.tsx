import { History } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { Pagination } from "@/features/rbac/components";
import { useSchemaHistory } from "@/features/schemas/hooks";
import type { SchemaChange } from "@/types/schemaRegistry";
import { errorMessage } from "@/utils/errors";
import { formatDateTime, formatEventType } from "@/utils/format";

export const HISTORY_PAGE_SIZE = 25;

export function HistoryTab({ schemaId }: { schemaId: string }) {
  const [page, setPage] = useState(0);
  const history = useSchemaHistory(schemaId, {
    offset: page * HISTORY_PAGE_SIZE,
    limit: HISTORY_PAGE_SIZE,
  });

  const columns: DataTableColumn<SchemaChange>[] = [
    {
      id: "version",
      header: "Version",
      cell: (change) =>
        change.version_incremented ? (
          <span className="font-mono text-sm font-medium text-fg">v{change.schema_version}</span>
        ) : (
          <span className="font-mono text-sm text-fg-muted" title="This action did not change the metadata version">
            v{change.schema_version}
          </span>
        ),
    },
    {
      id: "change",
      header: "Change",
      cell: (change) => (
        <div className="min-w-0">
          <p className="text-sm text-fg">{change.summary}</p>
          <Badge className="mt-1">{formatEventType(change.event_type)}</Badge>
        </div>
      ),
    },
    {
      id: "actor",
      header: "By",
      hideBelow: "sm",
      cell: (change) => change.actor_name ?? <span className="text-fg-muted">System</span>,
    },
    {
      id: "when",
      header: "When",
      cell: (change) => <span className="whitespace-nowrap text-sm">{formatDateTime(change.created_at)}</span>,
    },
  ];

  return (
    <DataTable
      caption="Schema change history, newest first"
      columns={columns}
      rows={history.data?.items}
      getRowId={(change) => change.id}
      loading={history.isPending}
      loadingLabel="Loading history"
      error={history.isError ? errorMessage(history.error, "The history could not be loaded.") : null}
      onRetry={() => void history.refetch()}
      retrying={history.isFetching}
      empty={{ icon: History, title: "No changes recorded" }}
      footer={
        history.data && (
          <Pagination page={page} pageSize={HISTORY_PAGE_SIZE} total={history.data.total} onPage={setPage} itemLabel="Changes" />
        )
      }
    />
  );
}
