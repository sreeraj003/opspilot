/** Module 07: external GET API connectors. Mirrors app/schemas/connector.py. Responses never contain secrets. */

export type AuthenticationType = "none" | "api_key" | "bearer_token" | "custom_header";
export type TestStatus = "untested" | "passed" | "failed";
export type ConnectorStatusFilter = "all" | "active" | "inactive";

export interface ResponseContract {
  format: "envelope" | "raw_array";
  success_field: string;
  data_field: string;
  metadata_field: string;
  error_field: string;
}

export interface QueryParam {
  name: string;
  value: string;
}

export interface CustomHeader {
  name: string;
  secret: boolean;
  /** Only for non-secret headers; secret values are never returned. */
  value: string | null;
  has_value: boolean;
}

export interface Connector {
  id: string;
  name: string;
  description: string | null;
  schema_source_id: string;
  schema_source_name: string | null;
  schema_source_available: boolean;
  base_url: string;
  endpoint_path: string;
  endpoint_host: string;
  http_method: "GET";
  sql_parameter_name: string;
  extra_query_params: QueryParam[];
  authentication_type: AuthenticationType;
  auth_header_name: string | null;
  has_credentials: boolean;
  credential_mask: string | null;
  custom_headers: CustomHeader[];
  health_check_path: string | null;
  response_contract: ResponseContract;
  timeout_seconds: number;
  connect_timeout_seconds: number;
  read_timeout_seconds: number;
  max_response_size_bytes: number;
  max_sql_length: number;
  is_active: boolean;
  last_tested_at: string | null;
  last_test_status: TestStatus;
  last_error_category: string | null;
  last_error_summary: string | null;
  destination_allowed: boolean;
  destination_problem: string | null;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  updated_by: string | null;
}

export interface CustomHeaderInput {
  name: string;
  secret: boolean;
  /** Omit for a secret header to keep the stored value. */
  value?: string;
}

export interface ConnectorInput {
  name: string;
  description: string | null;
  schema_source_id: string;
  base_url: string;
  endpoint_path: string;
  sql_parameter_name: string;
  extra_query_params: QueryParam[];
  authentication_type: AuthenticationType;
  auth_header_name: string | null;
  /** Omit on update to keep the stored credential. */
  credential?: string;
  custom_headers: CustomHeaderInput[];
  health_check_path: string | null;
  response_contract: ResponseContract;
  timeout_seconds: number;
  connect_timeout_seconds: number;
  read_timeout_seconds: number;
  max_response_size_bytes: number;
  max_sql_length: number;
}

export interface ConnectorListParams {
  offset: number;
  limit: number;
  search?: string;
  status?: ConnectorStatusFilter;
  authentication_type?: AuthenticationType | "";
  last_test_status?: TestStatus | "";
  sort?: "name" | "updated_at" | "created_at" | "last_tested_at";
  order?: "asc" | "desc";
}

export type TestStepName = "configuration" | "destination" | "network" | "tls" | "authentication" | "response_format";

export interface ConnectorTestStep {
  name: TestStepName;
  label: string;
  status: "passed" | "failed" | "skipped";
}

export interface ConnectorTestResult {
  connector_id: string;
  status: "passed" | "failed";
  method: "health_endpoint" | "test_query";
  message: string;
  error_category: string | null;
  http_status: number | null;
  row_count: number | null;
  duration_ms: number;
  tested_at: string;
  execution_reference: string;
  steps: ConnectorTestStep[];
}

export interface ConnectorHealth {
  connector_id: string;
  status: "ready" | "inactive" | "untested" | "failing" | "misconfigured";
  is_active: boolean;
  last_test_status: TestStatus;
  last_tested_at: string | null;
  last_error_category: string | null;
  last_error_summary: string | null;
  destination_allowed: boolean;
  destination_problem: string | null;
  credentials_available: boolean;
  schema_source_available: boolean;
  checked_at: string;
}

export interface ConnectorEnvironment {
  encryption_configured: boolean;
  allowed_hosts: string[];
  private_networks_approved: number;
  http_allowed: boolean;
  max_url_length: number;
  max_response_size_limit: number;
  max_timeout_seconds: number;
  test_query: string;
}

export interface ConnectorAuditEvent {
  id: string;
  connector_id: string | null;
  operation_type: string;
  status: "success" | "failure" | "rejected";
  requesting_user_id: string | null;
  requesting_user_name: string | null;
  execution_reference: string | null;
  duration_ms: number | null;
  response_size_bytes: number | null;
  http_status: number | null;
  row_count: number | null;
  error_category: string | null;
  schema_version: number | null;
  sql_length: number | null;
  details: Record<string, unknown>;
  created_at: string;
}
