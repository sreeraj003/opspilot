import { z } from "zod";

import type { AuthenticationType, Connector, ConnectorInput, CustomHeaderInput } from "@/types/connector";

/**
 * Client-side checks that mirror the backend's rules (app/connectors/validators/config.py) so mistakes
 * are caught before a request is sent. The server validates everything again, including the destination
 * allow-list and SSRF rules, and its answer is authoritative.
 */

export const HEADER_NAME_PATTERN = /^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$/;
export const PARAMETER_PATTERN = /^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/;
export const PATH_PATTERN = /^\/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$/;
const RESERVED_HEADERS = new Set(["host", "content-length", "content-type", "transfer-encoding", "connection", "cookie", "authorization"]);
const PRINTABLE = /^[\x20-\x7e\t]*$/;
const SECRET_CHARS = /^[\x21-\x7e]*$/;

const path = (label: string) =>
  z
    .string()
    .trim()
    .regex(PATH_PATTERN, `${label} must start with / and contain only URL path characters (no ? or #).`)
    .refine((value) => !value.split("/").some((segment) => segment === "." || segment === "..") && !value.includes("//"), {
      message: `${label} must not contain empty, "." or ".." segments.`,
    });

const headerRow = z
  .object({
    name: z
      .string()
      .trim()
      .min(1, "Enter a header name.")
      .regex(HEADER_NAME_PATTERN, "Use letters, digits and - _ . characters.")
      .refine((value) => !RESERVED_HEADERS.has(value.toLowerCase()), { message: "This header is managed by OpsPilot." }),
    value: z.string().max(4096, "Use at most 4096 characters.").regex(PRINTABLE, "Use printable characters only (no line breaks)."),
    secret: z.boolean(),
    /** A secret value is already stored for this header (editing only). */
    stored: z.boolean(),
  })
  .superRefine((row, context) => {
    if (!row.value.trim() && !(row.secret && row.stored)) {
      context.addIssue({ code: "custom", path: ["value"], message: "Enter a value." });
    }
  });

export function connectorFormSchema(existing: Connector | null) {
  return z
    .object({
      name: z.string().trim().min(1, "Enter a name.").max(150, "Use at most 150 characters."),
      description: z.string().trim().max(4000, "Use at most 4000 characters."),
      schema_source_id: z.string().min(1, "Choose the schema this connector serves."),
      base_url: z
        .string()
        .trim()
        .min(1, "Enter the base URL.")
        .regex(/^https?:\/\/[^\s/?#@]+(\/[^\s?#]*)?$/i, "Enter a URL such as https://company-backend.com (no query string)."),
      endpoint_path: path("The endpoint path"),
      sql_parameter_name: z.string().trim().regex(PARAMETER_PATTERN, "Start with a letter; use letters, digits, _ - or ."),
      health_check_path: z.union([z.literal(""), path("The health check path")]),
      response_format: z.enum(["envelope", "raw_array"]),
      authentication_type: z.enum(["none", "api_key", "bearer_token", "custom_header"]),
      auth_header_name: z.union([
        z.literal(""),
        z
          .string()
          .trim()
          .regex(HEADER_NAME_PATTERN, "Use letters, digits and - _ . characters.")
          .refine((value) => !RESERVED_HEADERS.has(value.toLowerCase()), { message: "This header is managed by OpsPilot." }),
      ]),
      credential: z.string().max(4096, "Use at most 4096 characters.").regex(SECRET_CHARS, "Use printable characters without spaces."),
      custom_headers: z.array(headerRow).max(20, "At most 20 headers."),
      timeout_seconds: z.number({ error: "Enter a number." }).int().min(1, "At least 1 second.").max(600, "At most 600 seconds."),
      connect_timeout_seconds: z.number({ error: "Enter a number." }).int().min(1, "At least 1 second.").max(600),
      read_timeout_seconds: z.number({ error: "Enter a number." }).int().min(1, "At least 1 second.").max(600),
      max_response_kb: z.number({ error: "Enter a number." }).int().min(1, "At least 1 KB.").max(512 * 1024),
      max_sql_length: z.number({ error: "Enter a number." }).int().min(1).max(65536, "At most 65536."),
    })
    .superRefine((values, context) => {
      const keeps = Boolean(existing?.has_credentials && existing.authentication_type === values.authentication_type);
      if (values.authentication_type !== "none" && !values.credential && !keeps) {
        context.addIssue({ code: "custom", path: ["credential"], message: "Enter the credential." });
      }
      if (values.authentication_type === "custom_header" && !values.auth_header_name) {
        context.addIssue({ code: "custom", path: ["auth_header_name"], message: "Enter the header that carries the credential." });
      }
      if (values.connect_timeout_seconds > values.timeout_seconds) {
        context.addIssue({ code: "custom", path: ["connect_timeout_seconds"], message: "Cannot be longer than the timeout." });
      }
      if (values.read_timeout_seconds > values.timeout_seconds) {
        context.addIssue({ code: "custom", path: ["read_timeout_seconds"], message: "Cannot be longer than the timeout." });
      }
      const seen = new Set<string>();
      const authHeader = ["api_key", "custom_header"].includes(values.authentication_type)
        ? (values.auth_header_name || (values.authentication_type === "api_key" ? "X-API-Key" : "")).toLowerCase()
        : "";
      values.custom_headers.forEach((header, index) => {
        const key = header.name.trim().toLowerCase();
        if (seen.has(key) || (authHeader && key === authHeader)) {
          context.addIssue({ code: "custom", path: ["custom_headers", index, "name"], message: "This header is already used." });
        }
        seen.add(key);
      });
    });
}

export type ConnectorFormValues = z.infer<ReturnType<typeof connectorFormSchema>>;

export function connectorDefaults(connector: Connector | null): ConnectorFormValues {
  return {
    name: connector?.name ?? "",
    description: connector?.description ?? "",
    schema_source_id: connector?.schema_source_id ?? "",
    base_url: connector?.base_url ?? "",
    endpoint_path: connector?.endpoint_path ?? "/api/sql/execute",
    sql_parameter_name: connector?.sql_parameter_name ?? "sql",
    health_check_path: connector?.health_check_path ?? "",
    response_format: connector?.response_contract.format ?? "envelope",
    authentication_type: connector?.authentication_type ?? "api_key",
    auth_header_name: connector?.auth_header_name ?? "",
    // Never prefilled: the server does not return credentials.
    credential: "",
    custom_headers: (connector?.custom_headers ?? []).map((header) => ({
      name: header.name,
      value: header.secret ? "" : (header.value ?? ""),
      secret: header.secret,
      stored: header.secret && header.has_value,
    })),
    timeout_seconds: connector?.timeout_seconds ?? 30,
    connect_timeout_seconds: connector?.connect_timeout_seconds ?? 5,
    read_timeout_seconds: connector?.read_timeout_seconds ?? 30,
    max_response_kb: Math.round((connector?.max_response_size_bytes ?? 5 * 1024 * 1024) / 1024),
    max_sql_length: connector?.max_sql_length ?? 4000,
  };
}

export function buildConnectorInput(values: ConnectorFormValues, existing: Connector | null): ConnectorInput {
  const auth = values.authentication_type as AuthenticationType;
  const headers: CustomHeaderInput[] = values.custom_headers.map((header) => {
    const value = header.secret ? header.value : header.value.trim();
    // A secret header left empty keeps its stored value on the server.
    return value ? { name: header.name.trim(), secret: header.secret, value } : { name: header.name.trim(), secret: header.secret };
  });
  const input: ConnectorInput = {
    name: values.name.trim(),
    description: values.description.trim() || null,
    schema_source_id: values.schema_source_id,
    base_url: values.base_url.trim().replace(/\/+$/, ""),
    endpoint_path: values.endpoint_path.trim(),
    sql_parameter_name: values.sql_parameter_name.trim(),
    extra_query_params: existing?.extra_query_params ?? [],
    authentication_type: auth,
    auth_header_name: auth === "api_key" || auth === "custom_header" ? values.auth_header_name.trim() || null : null,
    custom_headers: headers,
    health_check_path: values.health_check_path.trim() || null,
    response_contract: {
      ...(existing?.response_contract ?? { success_field: "success", data_field: "data", metadata_field: "metadata", error_field: "error" }),
      format: values.response_format,
    },
    timeout_seconds: values.timeout_seconds,
    connect_timeout_seconds: values.connect_timeout_seconds,
    read_timeout_seconds: values.read_timeout_seconds,
    max_response_size_bytes: values.max_response_kb * 1024,
    max_sql_length: values.max_sql_length,
  };
  // An omitted credential keeps the stored one.
  if (auth !== "none" && values.credential) input.credential = values.credential;
  return input;
}
