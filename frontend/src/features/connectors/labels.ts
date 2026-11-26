import type { AuthenticationType, TestStatus } from "@/types/connector";

export const AUTH_LABELS: Record<AuthenticationType, string> = {
  none: "None",
  api_key: "API key",
  bearer_token: "Bearer token",
  custom_header: "Custom header",
};

export const TEST_STATUS_LABELS: Record<TestStatus, string> = {
  untested: "Not tested",
  passed: "Test passed",
  failed: "Test failed",
};

const CATEGORY_LABELS: Record<string, string> = {
  configuration_invalid: "Configuration problem",
  encryption_unavailable: "Credentials unavailable",
  destination_rejected: "Destination not permitted",
  dns_resolution_failed: "Host name not found",
  connection_failed: "Connection failed",
  tls_failed: "TLS verification failed",
  timeout: "Timed out",
  authentication_failed: "Authentication rejected",
  redirect_rejected: "Redirect refused",
  http_error: "HTTP error",
  response_too_large: "Response too large",
  invalid_content_type: "Not a JSON response",
  malformed_response: "Unexpected response format",
  external_api_error: "Company API reported an error",
  request_too_large: "Request too large",
  validation_rejected: "Request refused",
};

export function errorCategoryLabel(category: string | null | undefined): string {
  if (!category) return "";
  return CATEGORY_LABELS[category] ?? category.replaceAll("_", " ");
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${Math.round((bytes / (1024 * 1024)) * 10) / 10} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}
