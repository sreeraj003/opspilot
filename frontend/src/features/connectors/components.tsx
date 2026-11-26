import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle2, CircleDashed, Plus, Trash2, XCircle } from "lucide-react";
import { useEffect, useMemo } from "react";
import { useFieldArray, useForm, useWatch } from "react-hook-form";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Dialog, DialogActions } from "@/components/ui/Dialog";
import { TextareaField, TextField } from "@/components/ui/Field";
import { FormField } from "@/components/ui/FormField";
import { Input, PasswordInput, Select } from "@/components/ui/Input";
import { StatusIndicator } from "@/components/ui/StatusIndicator";
import { useCreateConnector, useConnectorEnvironment, useUpdateConnector } from "@/features/connectors/hooks";
import { AUTH_LABELS, TEST_STATUS_LABELS } from "@/features/connectors/labels";
import {
  buildConnectorInput,
  connectorDefaults,
  connectorFormSchema,
  type ConnectorFormValues,
} from "@/features/connectors/validation";
import { useSchemaList } from "@/features/schemas/hooks";
import type { AuthenticationType, Connector, ConnectorTestResult, TestStatus } from "@/types/connector";
import { errorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";

// --- indicators ----------------------------------------------------------------------------------------

export function TestStatusBadge({ status }: { status: TestStatus }) {
  const tone = status === "passed" ? "success" : status === "failed" ? "danger" : "neutral";
  return <StatusIndicator tone={tone} label={TEST_STATUS_LABELS[status]} />;
}

/** The risk of sending SQL in a URL, shown wherever a connector is configured. */
export function GetQueryWarning() {
  return (
    <Alert tone="warning" title="SQL travels in the URL">
      OpsPilot sends each query as a GET query parameter. Proxies, gateways, load balancers and web servers often record full URLs,
      so the SQL (including any literal values in it) can end up in their logs. Ask your infrastructure team to redact the SQL
      parameter in those logs, and avoid sensitive literal values in questions. The company API must run queries read-only with a
      least-privilege database account.
    </Alert>
  );
}

// --- connection test result ------------------------------------------------------------------------------

const STEP_ICONS = { passed: CheckCircle2, failed: XCircle, skipped: CircleDashed } as const;
const STEP_CLASSES = { passed: "text-success-text", failed: "text-danger-text", skipped: "text-fg-subtle" } as const;

export function TestResultPanel({ result }: { result: ConnectorTestResult }) {
  const passed = result.status === "passed";
  return (
    <div className="space-y-3" aria-label="Connection test result">
      <Alert tone={passed ? "success" : "error"} title={passed ? "Connection test passed" : "Connection test failed"}>
        <p>{result.message}</p>
        <p className="mt-1 text-xs opacity-80">
          {result.method === "health_endpoint" ? "Checked the health endpoint" : "Ran the fixed test query"} in {result.duration_ms} ms
          {result.http_status ? `, HTTP ${result.http_status}` : ""} at {formatDateTime(result.tested_at)}.
        </p>
      </Alert>
      <ol className="space-y-1.5 text-sm">
        {result.steps.map((step) => {
          const Icon = STEP_ICONS[step.status];
          return (
            <li key={step.name} className="flex items-center gap-2">
              <Icon aria-hidden="true" className={`h-4 w-4 shrink-0 ${STEP_CLASSES[step.status]}`} />
              <span className={step.status === "skipped" ? "text-fg-muted" : "text-fg"}>{step.label}</span>
              <span className="sr-only">: {step.status}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// --- create / edit form -------------------------------------------------------------------------------------

export function ConnectorFormDialog({
  open,
  connector,
  onClose,
  onSaved,
}: {
  open: boolean;
  connector?: Connector | null;
  onClose: () => void;
  onSaved: (connector: Connector) => void;
}) {
  const create = useCreateConnector();
  const update = useUpdateConnector();
  const mutation = connector ? update : create;
  const environment = useConnectorEnvironment(open);
  const schemas = useSchemaList({ offset: 0, limit: 100, sort: "name", order: "asc" });
  const schema = useMemo(() => connectorFormSchema(connector ?? null), [connector]);
  const {
    register,
    handleSubmit,
    reset,
    control,
    formState: { errors, isSubmitting },
  } = useForm<ConnectorFormValues>({ resolver: zodResolver(schema), defaultValues: connectorDefaults(connector ?? null) });
  const headers = useFieldArray({ control, name: "custom_headers" });
  const authType = useWatch({ control, name: "authentication_type" }) as AuthenticationType;
  const headerValues = useWatch({ control, name: "custom_headers" });

  useEffect(() => {
    if (open) reset(connectorDefaults(connector ?? null));
    // Reset only when the dialog opens or switches to another connector.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, connector?.id]);

  const close = () => {
    create.reset();
    update.reset();
    onClose();
  };

  const onSubmit = handleSubmit(async (values) => {
    const input = buildConnectorInput(values, connector ?? null);
    try {
      const saved = connector ? await update.mutateAsync({ id: connector.id, input }) : await create.mutateAsync(input);
      onSaved(saved);
    } catch {
      // Shown from the mutation state.
    }
  });

  const keepsCredential = Boolean(connector?.has_credentials && connector.authentication_type === authType);
  const env = environment.data;

  return (
    <Dialog
      open={open}
      onClose={close}
      size="lg"
      title={connector ? "Edit connector" : "New connector"}
      description="How OpsPilot reaches your company's GET SQL endpoint. Credentials are encrypted on the server and never shown again."
    >
      <form onSubmit={onSubmit} noValidate className="space-y-5">
        {mutation.isError && (
          <Alert tone="error">{errorMessage(mutation.error, "The connector could not be saved.")}</Alert>
        )}
        {env && !env.encryption_configured && (
          <Alert tone="warning" title="Credential encryption is not configured">
            The server has no CONNECTOR_ENCRYPTION_KEY, so connectors with credentials cannot be saved. Ask the server administrator
            to set it.
          </Alert>
        )}
        <GetQueryWarning />

        <fieldset className="space-y-4">
          <legend className="text-sm font-semibold text-fg">Connector</legend>
          <TextField label="Connector name" required error={errors.name?.message} {...register("name")} />
          <TextareaField label="Description (optional)" rows={2} error={errors.description?.message} {...register("description")} />
          <FormField
            label="Associated schema"
            required
            error={errors.schema_source_id?.message}
            description={schemas.isError ? "Schemas could not be loaded. You need permission to view schemas." : undefined}
          >
            <Select {...register("schema_source_id")}>
              <option value="">Choose a schema</option>
              {connector && !schemas.data?.items.some((s) => s.id === connector.schema_source_id) && (
                <option value={connector.schema_source_id}>{connector.schema_source_name ?? "Current schema"}</option>
              )}
              {schemas.data?.items.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          </FormField>
        </fieldset>

        <fieldset className="space-y-4">
          <legend className="text-sm font-semibold text-fg">Endpoint</legend>
          <TextField
            label="Base URL"
            required
            placeholder="https://company-backend.com"
            className="font-mono"
            error={errors.base_url?.message}
            hint={
              env?.allowed_hosts.length
                ? `Approved hosts on this server: ${env.allowed_hosts.join(", ")}`
                : env
                  ? "No hosts are approved on this server yet (CONNECTOR_ALLOWED_HOSTS)."
                  : undefined
            }
            {...register("base_url")}
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label="Endpoint path"
              required
              placeholder="/api/sql/execute"
              className="font-mono"
              error={errors.endpoint_path?.message}
              {...register("endpoint_path")}
            />
            <TextField
              label="SQL parameter name"
              required
              className="font-mono"
              hint="The query parameter that carries the SQL, e.g. sql."
              error={errors.sql_parameter_name?.message}
              {...register("sql_parameter_name")}
            />
          </div>
          <TextField
            label="Health check path (optional)"
            placeholder="/api/health"
            className="font-mono"
            hint="Used by the connection test when set; otherwise a fixed test query is sent."
            error={errors.health_check_path?.message}
            {...register("health_check_path")}
          />
          <FormField label="Response format" description="Envelope: {success, data, metadata}. Raw array: a JSON array of row objects.">
            <Select {...register("response_format")}>
              <option value="envelope">Envelope (success, data, metadata)</option>
              <option value="raw_array">Raw JSON array of rows</option>
            </Select>
          </FormField>
        </fieldset>

        <fieldset className="space-y-4">
          <legend className="text-sm font-semibold text-fg">Authentication</legend>
          <FormField label="Authentication type" error={errors.authentication_type?.message}>
            <Select {...register("authentication_type")}>
              {Object.entries(AUTH_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </FormField>
          {(authType === "api_key" || authType === "custom_header") && (
            <TextField
              label="Header name"
              required={authType === "custom_header"}
              className="font-mono"
              placeholder={authType === "api_key" ? "X-API-Key" : "X-Company-Auth"}
              error={errors.auth_header_name?.message}
              {...register("auth_header_name")}
            />
          )}
          {authType !== "none" && (
            <FormField
              label={authType === "bearer_token" ? "Bearer token" : authType === "api_key" ? "API key" : "Credential value"}
              required={!keepsCredential}
              error={errors.credential?.message}
              description={
                keepsCredential
                  ? "A credential is stored (hidden). Leave this empty to keep it, or enter a new one to replace it."
                  : "Stored encrypted. It is never shown again after saving."
              }
            >
              <PasswordInput
                revealLabel="credential"
                autoComplete="new-password"
                placeholder={keepsCredential ? "•••••••• (stored)" : ""}
                {...register("credential")}
              />
            </FormField>
          )}
        </fieldset>

        <fieldset className="space-y-3">
          <legend className="text-sm font-semibold text-fg">Custom headers</legend>
          {headers.fields.length === 0 && <p className="text-sm text-fg-muted">No custom headers.</p>}
          {headers.fields.map((field, index) => {
            const row = headerValues?.[index];
            const keepsSecret = Boolean(row?.secret && row?.stored);
            return (
              <div key={field.id} className="grid gap-2 rounded-md border border-line p-3 sm:grid-cols-[1fr_1fr_auto] sm:items-start">
                <FormField label={`Header ${index + 1} name`} hideLabel error={errors.custom_headers?.[index]?.name?.message}>
                  <Input placeholder="Header name" className="font-mono" {...register(`custom_headers.${index}.name`)} />
                </FormField>
                <FormField label={`Header ${index + 1} value`} hideLabel error={errors.custom_headers?.[index]?.value?.message}>
                  {row?.secret ? (
                    <PasswordInput
                      revealLabel={`header ${index + 1} value`}
                      autoComplete="new-password"
                      placeholder={keepsSecret ? "•••••••• (stored)" : "Secret value"}
                      {...register(`custom_headers.${index}.value`)}
                    />
                  ) : (
                    <Input placeholder="Value" {...register(`custom_headers.${index}.value`)} />
                  )}
                </FormField>
                <div className="flex items-center gap-3 sm:pt-2">
                  <Checkbox label="Secret" {...register(`custom_headers.${index}.secret`)} />
                  <Button variant="ghost" size="sm" aria-label={`Remove header ${index + 1}`} onClick={() => headers.remove(index)}>
                    <Trash2 aria-hidden="true" className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            );
          })}
          <Button
            variant="secondary"
            size="sm"
            onClick={() => headers.append({ name: "", value: "", secret: false, stored: false })}
            disabled={headers.fields.length >= 20}
          >
            <Plus aria-hidden="true" className="h-4 w-4" /> Add header
          </Button>
        </fieldset>

        <fieldset className="space-y-4">
          <legend className="text-sm font-semibold text-fg">Limits</legend>
          <div className="grid gap-4 sm:grid-cols-3">
            <TextField
              label="Timeout (seconds)"
              type="number"
              inputMode="numeric"
              error={errors.timeout_seconds?.message}
              hint={env ? `At most ${env.max_timeout_seconds}.` : undefined}
              {...register("timeout_seconds", { valueAsNumber: true })}
            />
            <TextField
              label="Connect timeout (seconds)"
              type="number"
              inputMode="numeric"
              error={errors.connect_timeout_seconds?.message}
              {...register("connect_timeout_seconds", { valueAsNumber: true })}
            />
            <TextField
              label="Read timeout (seconds)"
              type="number"
              inputMode="numeric"
              error={errors.read_timeout_seconds?.message}
              {...register("read_timeout_seconds", { valueAsNumber: true })}
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label="Maximum response size (KB)"
              type="number"
              inputMode="numeric"
              error={errors.max_response_kb?.message}
              {...register("max_response_kb", { valueAsNumber: true })}
            />
            <TextField
              label="Maximum SQL length (characters)"
              type="number"
              inputMode="numeric"
              error={errors.max_sql_length?.message}
              hint="Longer SQL is refused, never truncated."
              {...register("max_sql_length", { valueAsNumber: true })}
            />
          </div>
        </fieldset>

        <DialogActions>
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" loading={isSubmitting}>
            {connector ? "Save changes" : "Create connector"}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}
