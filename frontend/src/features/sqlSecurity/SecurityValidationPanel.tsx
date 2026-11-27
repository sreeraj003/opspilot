import { ShieldCheck, ShieldX } from "lucide-react";
import type { ReactNode } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { StatusIndicator, type StatusTone } from "@/components/ui/StatusIndicator";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { SqlCode } from "@/features/sqlGeneration/SqlCode";
import { useApproval, useRevokeApproval, useValidateGeneration } from "@/features/sqlSecurity/hooks";
import type { SqlApproval, SqlSecurityValidation } from "@/types/sqlSecurity";
import { errorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";

/**
 * Module 11 security validation, inside the SQL generation preview.
 *
 * Every state shown here comes from the server. The browser never decides that a query is approved, never sends
 * SQL, and offers no execute action: the question-to-answer workflow that will run approved SQL does not exist yet.
 */

type Display = { tone: StatusTone; label: string };

const AWAITING: Display = { tone: "info", label: "Awaiting validation" };

function display(validation: SqlSecurityValidation, approval: SqlApproval | null): Display {
  if (validation.status === "approved" && approval) {
    switch (approval.status) {
      case "approved":
        return approval.is_usable ? { tone: "success", label: "Approved" } : { tone: "warning", label: "Approval expired" };
      case "expired":
        return { tone: "warning", label: "Approval expired" };
      case "revoked":
        return { tone: "neutral", label: "Revoked" };
      case "invalidated":
        return { tone: "warning", label: "Approval invalidated" };
      case "consumed":
      case "executed":
        return { tone: "neutral", label: "Approval used" };
    }
  }
  switch (validation.status) {
    case "approval_expired":
      return { tone: "warning", label: "Approval expired" };
    case "clarification_required":
      return { tone: "warning", label: "Clarification required" };
    case "validation_failed":
      return { tone: "danger", label: "Validation failed" };
    default:
      return { tone: "danger", label: "Rejected" };
  }
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-4">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="min-w-0 break-words text-fg sm:text-right">{children}</dd>
    </div>
  );
}

function ApprovalDetails({ validation, approval }: { validation: SqlSecurityValidation; approval: SqlApproval }) {
  const revoke = useRevokeApproval();
  return (
    <div className="space-y-2">
      <dl className="space-y-2 text-sm" aria-label="Approval details">
        <Row label="Approval status">{approval.is_usable ? "Usable once, by you, for this exact query" : "Not usable"}</Row>
        <Row label="Expires">{formatDateTime(approval.expires_at)}</Row>
        <Row label="Schema version">{approval.schema_version}</Row>
        <Row label="Validated">{formatDateTime(validation.validated_at)}</Row>
        <Row label="Query fingerprint">
          <span className="font-mono break-all">{approval.exact_sql_hash}</span>
        </Row>
      </dl>
      {revoke.isError && <Alert tone="error">{errorMessage(revoke.error, "The approval could not be revoked.")}</Alert>}
      {approval.is_usable && (
        <Button size="sm" variant="secondary" loading={revoke.isPending} onClick={() => revoke.mutate(approval.approval_id)}>
          Revoke approval
        </Button>
      )}
    </div>
  );
}

function Result({ validation }: { validation: SqlSecurityValidation }) {
  const approvalQuery = useApproval(validation.approval?.approval_id ?? null, validation.approval);
  const approval = approvalQuery.data ?? validation.approval;
  const status = display(validation, approval);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2" aria-label="Security status">
        {validation.approved ? (
          <ShieldCheck aria-hidden="true" className="h-4 w-4 text-success-text" />
        ) : (
          <ShieldX aria-hidden="true" className="h-4 w-4 text-danger-text" />
        )}
        <StatusIndicator tone={status.tone} label={status.label} />
        {validation.schema_version !== null && <Badge>Schema version {validation.schema_version}</Badge>}
      </div>
      <p className="text-sm text-fg-secondary">{validation.summary}</p>

      {validation.validation_errors.length > 0 && (
        <div>
          <h4 className="mb-1 text-sm font-semibold text-fg">Why it was not approved</h4>
          <ul aria-label="Security errors" className="space-y-1 text-sm text-fg-secondary">
            {validation.validation_errors.map((error) => (
              <li key={`${error.code}-${error.message}`}>{error.message}</li>
            ))}
          </ul>
        </div>
      )}

      {validation.warnings.length > 0 && (
        <Alert tone="warning" title="Warnings">
          <ul aria-label="Security warnings" className="list-disc space-y-1 pl-5">
            {validation.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Alert>
      )}

      {validation.applied_row_restrictions.length > 0 && (
        <div>
          <h4 className="mb-1 text-sm font-semibold text-fg">Row restrictions applied</h4>
          <ul aria-label="Row restrictions" className="flex flex-wrap gap-1.5">
            {validation.applied_row_restrictions.map((table) => (
              <li key={table}>
                <Badge mono>{table}</Badge>
              </li>
            ))}
          </ul>
        </div>
      )}

      {validation.approved && validation.sql_visible && validation.approved_sql && (
        <div className="space-y-1">
          <h4 className="text-sm font-semibold text-fg">Approved SQL (with your row restrictions)</h4>
          <SqlCode sql={validation.approved_sql} label="Approved SQL" />
        </div>
      )}

      {validation.approved && approval && <ApprovalDetails validation={validation} approval={approval} />}

      {validation.internal_reasons && validation.internal_reasons.length > 0 && (
        <div>
          <h4 className="mb-1 text-sm font-semibold text-fg">Internal reasons (auditors only)</h4>
          <ul aria-label="Internal reasons" className="space-y-1 text-sm text-fg-secondary">
            {validation.internal_reasons.map((reason) => (
              <li key={`${reason.code}-${reason.reason}`}>
                <Badge mono>{reason.code}</Badge> {reason.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-xs text-fg-muted">
        Running queries is not available yet. An approval is single use, expires quickly and is checked again on the server
        before anything is sent to your company database.
      </p>
    </div>
  );
}

export function SecurityValidationPanel({ generationId }: { generationId: string }) {
  const { hasPermission } = usePermissions();
  const validate = useValidateGeneration(generationId);
  const canValidate = hasPermission("ai.sql.validate");
  const validation = validate.data;

  return (
    <section aria-label="Security validation" className="space-y-3 rounded-md border border-line p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-fg">Security validation</h3>
        {canValidate && (
          <Button size="sm" variant={validation ? "secondary" : "primary"} loading={validate.isPending} onClick={() => validate.mutate()}>
            {validation ? "Validate again" : "Validate SQL"}
          </Button>
        )}
      </div>
      {validate.isError && <Alert tone="error">{errorMessage(validate.error, "The query could not be validated.")}</Alert>}
      {validation ? (
        <Result validation={validation} />
      ) : (
        <div className="space-y-1">
          <StatusIndicator tone={AWAITING.tone} label={AWAITING.label} />
          <p className="text-sm text-fg-muted">
            {canValidate
              ? "The server checks the query against your permissions, the registered schema and the security rules."
              : "You do not have permission to run security validation."}
          </p>
        </div>
      )}
    </section>
  );
}
