import { ArrowLeft } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/Layout";
import { Spinner } from "@/components/ui/Spinner";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { PolicyBuilder } from "@/features/rbac/PolicyBuilder";
import { ActiveBadge, Card, Checkbox, SystemTag } from "@/features/rbac/components";
import { INPUT_CLASSES, SELECT_CLASSES } from "@/features/rbac/fieldClasses";
import { formatEventType } from "@/utils/format";
import { KEY_PATTERN, slugify } from "@/features/rbac/keys";
import { useCreatePermission, usePermission, useUpdatePermission, useValidatePolicy } from "@/features/rbac/hooks";
import {
  DATA_RESOURCE_TYPE,
  buildPolicy,
  draftFromPolicy,
  emptyDraft,
  summarizePolicy,
  validateDraft,
  type DraftIssue,
  type PolicyDraft,
} from "@/features/rbac/policyBuilder";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { ApiError } from "@/services/apiClient";
import type { PermissionDetail } from "@/types/rbac";
import { errorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";

const RESOURCE_PATTERN = /^[a-z][a-z0-9_]*$/;
const COMMON_RESOURCES = [
  { value: DATA_RESOURCE_TYPE, label: "Database data (tables, columns, rows)" },
  { value: "reports", label: "Reports" },
  { value: "dashboards", label: "Dashboards" },
];

interface Details {
  name: string;
  key: string;
  description: string;
  resourceType: string;
  action: string;
  expiresOn: string;
  isActive: boolean;
}

function emptyDetails(): Details {
  return { name: "", key: "", description: "", resourceType: DATA_RESOURCE_TYPE, action: "view", expiresOn: "", isActive: true };
}

function detailsFrom(permission: PermissionDetail): Details {
  return {
    name: permission.name,
    key: permission.key,
    description: permission.description ?? "",
    resourceType: permission.resource_type,
    action: permission.action,
    expiresOn: permission.expires_at ? permission.expires_at.slice(0, 10) : "",
    isActive: permission.is_active,
  };
}

function detailIssues(details: Details, isNew: boolean): DraftIssue[] {
  const issues: DraftIssue[] = [];
  if (!details.name.trim()) issues.push({ path: "Name", message: "Enter a name." });
  if (isNew && !KEY_PATTERN.test(details.key)) issues.push({ path: "Key", message: "Use lowercase words separated by dots, such as sales.karnataka.view." });
  if (!RESOURCE_PATTERN.test(details.resourceType)) issues.push({ path: "Resource", message: "Use lowercase letters, digits and underscores." });
  else if (details.resourceType === "system") issues.push({ path: "Resource", message: "'system' is reserved for built-in permissions." });
  if (!KEY_PATTERN.test(details.action)) issues.push({ path: "Action", message: "Use a lowercase action such as view." });
  return issues;
}

/** Field errors from the server (location + message), shown next to the review. */
function serverIssues(error: unknown): DraftIssue[] {
  if (!(error instanceof ApiError) || !Array.isArray(error.details)) return [];
  return (error.details as { location?: unknown[]; message?: string }[]).map((item) => ({
    path: Array.isArray(item.location) ? item.location.filter((part) => part !== "body" && part !== "policy_definition").join(" › ") || "Policy" : "Policy",
    message: item.message ?? "Invalid value.",
  }));
}

function IssueList({ title, issues }: { title: string; issues: DraftIssue[] }) {
  return (
    <Alert tone="error">
      <p className="font-medium">{title}</p>
      <ul className="mt-1 list-inside list-disc" aria-label="Problems">
        {issues.map((issue, index) => (
          <li key={index}>
            <span className="font-medium">{issue.path}:</span> {issue.message}
          </li>
        ))}
      </ul>
    </Alert>
  );
}

function ReadOnlyPermission({ permission }: { permission: PermissionDetail }) {
  return (
    <Card title="Policy" description="This permission is read-only.">
      <ul className="mb-4 list-inside list-disc space-y-1 text-sm text-fg-secondary" aria-label="Policy summary">
        {summarizePolicy(permission.resource_type, permission.policy_definition).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <pre aria-label="Policy JSON" className="overflow-x-auto rounded-control bg-surface-muted p-3 font-mono text-xs text-fg">
        {JSON.stringify(permission.policy_definition, null, 2)}
      </pre>
    </Card>
  );
}

export function PermissionEditor({ existing }: { existing?: PermissionDetail }) {
  const navigate = useNavigate();
  const isNew = !existing;
  const [details, setDetails] = useState<Details>(() => (existing ? detailsFrom(existing) : emptyDetails()));
  const [draft, setDraft] = useState<PolicyDraft>(() => (existing ? draftFromPolicy(existing.policy_definition) : emptyDraft()));
  const [keyEdited, setKeyEdited] = useState(!isNew);
  const [step, setStep] = useState<"edit" | "review">("edit");
  const [issues, setIssues] = useState<DraftIssue[]>([]);
  const [saved, setSaved] = useState(false);
  const validate = useValidatePolicy();
  const create = useCreatePermission();
  const update = useUpdatePermission(existing?.id ?? "");
  const save = isNew ? create : update;

  // When the server returns a newer version (after a save or refetch), start from it.
  const [source, setSource] = useState(existing);
  if (existing !== source) {
    setSource(existing);
    if (existing) {
      setDetails(detailsFrom(existing));
      setDraft(draftFromPolicy(existing.policy_definition));
    }
  }

  const set = (changes: Partial<Details>) => {
    setSaved(false);
    setDetails((previous) => ({ ...previous, ...changes }));
  };
  const policy = buildPolicy(details.resourceType, draft);
  const normalized = validate.data?.policy_definition ?? policy;

  const review = () => {
    setSaved(false);
    const found = [...detailIssues(details, isNew), ...validateDraft(details.resourceType, draft)];
    setIssues(found);
    if (found.length) return;
    validate.mutate({ resourceType: details.resourceType, policy }, { onSuccess: () => setStep("review") });
  };

  const submit = () => {
    const expiresAt = details.expiresOn ? new Date(`${details.expiresOn}T23:59:59`).toISOString() : null;
    const body = {
      name: details.name.trim(),
      description: details.description.trim() || null,
      resource_type: details.resourceType,
      action: details.action,
      policy_definition: normalized,
      is_active: details.isActive,
      expires_at: expiresAt,
    };
    if (isNew) {
      create.mutate({ ...body, key: details.key }, { onSuccess: (created) => navigate(`/admin/permissions/${created.id}`, { replace: true }) });
    } else {
      update.mutate(body, {
        onSuccess: () => {
          setSaved(true);
          setStep("edit");
        },
      });
    }
  };

  const validationIssues = serverIssues(validate.error);
  const saveIssues = serverIssues(save.error);

  if (step === "review") {
    return (
      <div className="space-y-6">
        <Card title="Review permission" description="Check what this permission allows before saving. The server validated this policy.">
          <dl className="mb-5 grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-fg-muted">Name</dt>
              <dd className="font-medium text-fg">{details.name}</dd>
            </div>
            <div>
              <dt className="text-fg-muted">Key</dt>
              <dd className="font-mono text-fg">{details.key}</dd>
            </div>
            <div>
              <dt className="text-fg-muted">Resource and action</dt>
              <dd className="font-mono text-fg">
                {details.resourceType} · {details.action}
              </dd>
            </div>
            <div>
              <dt className="text-fg-muted">Status</dt>
              <dd className="text-fg">
                {details.isActive ? "Active" : "Inactive"}
                {details.expiresOn ? `, expires ${details.expiresOn}` : ""}
              </dd>
            </div>
          </dl>
          <h3 className="text-sm font-medium text-fg-secondary">What it does</h3>
          <ul className="mt-2 list-inside list-disc space-y-1 text-sm text-fg-secondary" aria-label="Policy summary">
            {summarizePolicy(details.resourceType, normalized).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <details className="mt-5">
            <summary className="cursor-pointer text-sm font-medium text-fg-secondary">Policy JSON (read-only)</summary>
            <pre aria-label="Policy JSON" className="mt-2 overflow-x-auto rounded-control bg-surface-muted p-3 font-mono text-xs text-fg">
              {JSON.stringify(normalized, null, 2)}
            </pre>
          </details>
        </Card>
        {save.isError &&
          (saveIssues.length ? (
            <IssueList title={errorMessage(save.error)} issues={saveIssues} />
          ) : (
            <Alert tone="error">{errorMessage(save.error, "The permission could not be saved.")}</Alert>
          ))}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setStep("edit")}>
            Back to editing
          </Button>
          <Button loading={save.isPending} onClick={submit}>
            {isNew ? "Create permission" : "Save permission"}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {saved && <Alert tone="success">Permission saved. Users with it see the change immediately.</Alert>}
      <Card title="Details">
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Name"
            value={details.name}
            placeholder="View Karnataka sales"
            onChange={(event) => set({ name: event.target.value, ...(keyEdited ? {} : { key: slugify(event.target.value).replace(/_/g, ".") }) })}
          />
          <TextField
            label="Key"
            className="font-mono"
            value={details.key}
            disabled={!isNew}
            placeholder="sales.karnataka.view"
            hint={isNew ? "A stable identifier. It cannot be changed later." : "Keys cannot be changed."}
            onChange={(event) => {
              setKeyEdited(true);
              set({ key: event.target.value.trim() });
            }}
          />
          <TextField label="Description (optional)" className="sm:col-span-2" value={details.description} onChange={(event) => set({ description: event.target.value })} />
          <div>
            <label htmlFor="resource-type" className="mb-1.5 block text-sm font-medium text-fg-secondary">
              Resource
            </label>
            <input
              id="resource-type"
              list="resource-types"
              value={details.resourceType}
              onChange={(event) => set({ resourceType: event.target.value.trim() })}
              className={`${INPUT_CLASSES} font-mono`}
            />
            <datalist id="resource-types">
              {COMMON_RESOURCES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </datalist>
            <p className="mt-1.5 text-xs text-fg-muted">Use database_data for access to tables, columns and rows.</p>
          </div>
          <div>
            <label htmlFor="action" className="mb-1.5 block text-sm font-medium text-fg-secondary">
              Action
            </label>
            <select id="action" value={details.action} onChange={(event) => set({ action: event.target.value })} className={`${SELECT_CLASSES} w-full`}>
              {Array.from(new Set(["view", "export", "manage", details.action])).map((action) => (
                <option key={action} value={action}>
                  {action}
                </option>
              ))}
            </select>
          </div>
          <TextField label="Expires on (optional)" type="date" value={details.expiresOn} onChange={(event) => set({ expiresOn: event.target.value })} />
          <div className="flex items-end pb-2">
            <Checkbox label="Active" checked={details.isActive} onChange={(isActive) => set({ isActive })} />
          </div>
        </div>
      </Card>

      <Card title="Access" description="Build the rule with the form below. No JSON needed.">
        <PolicyBuilder
          resourceType={details.resourceType}
          draft={draft}
          onChange={(next) => {
            setSaved(false);
            setDraft(next);
          }}
        />
      </Card>

      {issues.length > 0 && <IssueList title="Fix these before reviewing:" issues={issues} />}
      {validate.isError &&
        (validationIssues.length ? (
          <IssueList title="The server rejected this policy:" issues={validationIssues} />
        ) : (
          <Alert tone="error">{errorMessage(validate.error, "The policy could not be checked.")}</Alert>
        ))}

      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={() => navigate("/admin/permissions")}>
          Cancel
        </Button>
        <Button loading={validate.isPending} onClick={review}>
          Review
        </Button>
      </div>
    </div>
  );
}

export function PermissionEditorPage() {
  const { permissionId } = useParams();
  const isNew = !permissionId || permissionId === "new";
  const { hasPermission } = usePermissions();
  const canManage = hasPermission("permissions:manage");
  const detail = usePermission(permissionId ?? "", !isNew);
  useDocumentTitle(isNew ? "Create permission" : (detail.data?.name ?? "Permission"));

  const back = (
    <Link to="/admin/permissions" className="inline-flex items-center gap-1.5 rounded-xs text-sm text-fg-muted hover:text-fg">
      <ArrowLeft aria-hidden="true" className="h-4 w-4" /> All permissions
    </Link>
  );

  if (isNew) {
    if (!canManage) {
      return (
        <div className="space-y-4">
          {back}
          <Alert tone="error">You do not have permission to create permissions.</Alert>
        </div>
      );
    }
    return (
      <div className="space-y-section">
        <PageHeader eyebrow={back} title="Create permission" description="Describe what may be done and on which data, review it, then save." />
        <PermissionEditor />
      </div>
    );
  }

  if (detail.isPending) return <Spinner label="Loading permission" />;
  if (detail.isError) {
    const missing = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div className="space-y-4">
        {back}
        <Alert tone="error">{missing ? "This permission does not exist or was deleted." : errorMessage(detail.error)}</Alert>
      </div>
    );
  }
  const permission = detail.data;
  const editable = canManage && !permission.is_system;

  return (
    <div className="space-y-section">
      <PageHeader
        eyebrow={back}
        title={permission.name}
        description={<span className="font-mono">{permission.key}</span>}
        meta={
          <>
            <ActiveBadge active={permission.is_active} />
            {permission.is_system && <SystemTag />}
          </>
        }
      />
      <div className="grid gap-section lg:grid-cols-3">
        <div className="min-w-0 lg:col-span-2">{editable ? <PermissionEditor existing={permission} /> : <ReadOnlyPermission permission={permission} />}</div>
        <div className="min-w-0 space-y-section">
          <Card title="Roles" description={permission.roles.length ? undefined : "No role has this permission."}>
            {permission.roles.length > 0 && (
              <ul className="space-y-1 text-sm" aria-label="Roles with this permission">
                {permission.roles.map((role) => (
                  <li key={role.id}>
                    <Link to={`/admin/roles/${role.id}`} className="font-medium text-fg hover:underline">
                      {role.name}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-3 text-xs text-fg-muted">Granted directly to {permission.user_count === 1 ? "1 user" : `${permission.user_count} users`}.</p>
          </Card>
          <Card title="History">
            {permission.history.length === 0 ? (
              <p className="text-sm text-fg-muted">No changes recorded.</p>
            ) : (
              <ol className="space-y-2 text-sm">
                {permission.history.map((event) => (
                  <li key={event.id}>
                    <p className="text-fg">{formatEventType(event.event_type)}</p>
                    <p className="text-xs text-fg-muted">{formatDateTime(event.created_at)}</p>
                  </li>
                ))}
              </ol>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
