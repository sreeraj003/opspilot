import type { Connector, ConnectorAuditEvent, ConnectorEnvironment, ConnectorHealth, ConnectorTestResult } from "@/types/connector";

export const CONNECTOR_ID = "77777777-7777-4777-8777-777777777777";
export const CONNECTOR_SCHEMA_ID = "11111111-1111-4111-8111-111111111111";

const at = "2026-11-20T08:00:00Z";

export function makeConnector(overrides: Partial<Connector> = {}): Connector {
  return {
    id: CONNECTOR_ID,
    name: "Company SQL API",
    description: "Reporting replica",
    schema_source_id: CONNECTOR_SCHEMA_ID,
    schema_source_name: "Sales DB",
    schema_source_available: true,
    base_url: "https://company-backend.example.com",
    endpoint_path: "/api/sql/execute",
    endpoint_host: "company-backend.example.com",
    http_method: "GET",
    sql_parameter_name: "sql",
    extra_query_params: [],
    authentication_type: "api_key",
    auth_header_name: "X-API-Key",
    has_credentials: true,
    credential_mask: "••••••••",
    custom_headers: [
      { name: "X-Tenant", secret: false, value: "acme", has_value: true },
      { name: "X-Signature", secret: true, value: null, has_value: true },
    ],
    health_check_path: null,
    response_contract: { format: "envelope", success_field: "success", data_field: "data", metadata_field: "metadata", error_field: "error" },
    timeout_seconds: 30,
    connect_timeout_seconds: 5,
    read_timeout_seconds: 30,
    max_response_size_bytes: 5 * 1024 * 1024,
    max_sql_length: 4000,
    is_active: false,
    last_tested_at: null,
    last_test_status: "untested",
    last_error_category: null,
    last_error_summary: null,
    destination_allowed: true,
    destination_problem: null,
    created_at: at,
    updated_at: at,
    created_by: null,
    updated_by: null,
    ...overrides,
  };
}

export function makeEnvironment(overrides: Partial<ConnectorEnvironment> = {}): ConnectorEnvironment {
  return {
    encryption_configured: true,
    allowed_hosts: ["company-backend.example.com"],
    private_networks_approved: 0,
    http_allowed: false,
    max_url_length: 8192,
    max_response_size_limit: 50 * 1024 * 1024,
    max_timeout_seconds: 120,
    test_query: "SELECT 1 AS opspilot_connection_test",
    ...overrides,
  };
}

export function makeHealth(overrides: Partial<ConnectorHealth> = {}): ConnectorHealth {
  return {
    connector_id: CONNECTOR_ID,
    status: "inactive",
    is_active: false,
    last_test_status: "untested",
    last_tested_at: null,
    last_error_category: null,
    last_error_summary: null,
    destination_allowed: true,
    destination_problem: null,
    credentials_available: true,
    schema_source_available: true,
    checked_at: at,
    ...overrides,
  };
}

const STEPS = [
  ["configuration", "Configuration is valid"],
  ["destination", "Destination host is permitted"],
  ["network", "DNS and network connection"],
  ["tls", "TLS certificate verified"],
  ["authentication", "Authentication accepted"],
  ["response_format", "Response has the expected format"],
] as const;

export function makeTestResult(failedAt?: (typeof STEPS)[number][0], overrides: Partial<ConnectorTestResult> = {}): ConnectorTestResult {
  const failedIndex = failedAt ? STEPS.findIndex(([name]) => name === failedAt) : -1;
  return {
    connector_id: CONNECTOR_ID,
    status: failedAt ? "failed" : "passed",
    method: "test_query",
    message: failedAt ? "The company API rejected the connector's credentials." : "Connection test passed: the test query returned 1 row.",
    error_category: failedAt ? "authentication_failed" : null,
    http_status: failedAt ? 401 : 200,
    row_count: failedAt ? null : 1,
    duration_ms: 42,
    tested_at: at,
    execution_reference: "88888888-8888-4888-8888-888888888888",
    steps: STEPS.map(([name, label], index) => ({
      name,
      label,
      status: failedIndex < 0 || index < failedIndex ? "passed" : index === failedIndex ? "failed" : "skipped",
    })),
    ...overrides,
  };
}

export function makeAuditEvent(overrides: Partial<ConnectorAuditEvent> = {}): ConnectorAuditEvent {
  return {
    id: "99999999-9999-4999-8999-999999999999",
    connector_id: CONNECTOR_ID,
    operation_type: "created",
    status: "success",
    requesting_user_id: null,
    requesting_user_name: "Dana Operator",
    execution_reference: null,
    duration_ms: null,
    response_size_bytes: null,
    http_status: null,
    row_count: null,
    error_category: null,
    schema_version: null,
    sql_length: null,
    details: {},
    created_at: at,
    ...overrides,
  };
}
