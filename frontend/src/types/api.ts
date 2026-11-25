/** Contracts mirrored from the backend response schemas (backend/app/schemas). */

export interface AppInfoResponse {
  name: string;
  description: string;
  version: string;
  environment: string;
  docs_url: string;
}

export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
  environment: string;
  timestamp: string;
}

export type DatabaseStatus = "ok" | "unavailable" | "not_configured";

export interface DatabaseHealthResponse {
  status: DatabaseStatus;
  database: string;
  latency_ms: number | null;
  detail: string | null;
  timestamp: string;
}

export interface ApiErrorDetail {
  code: string;
  message: string;
  details?: unknown;
  request_id?: string;
}

/** Envelope the backend returns for every failed request. */
export interface ApiErrorResponse {
  success: false;
  error: ApiErrorDetail;
}
