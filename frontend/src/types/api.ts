/** Contracts mirrored from the backend's /api/v1 schemas (see backend/app/schemas). */

export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
  environment: string;
  timestamp: string;
}

export type CheckStatus = "ok" | "error" | "not_configured";

export interface DependencyCheck {
  name: string;
  status: CheckStatus;
  required: boolean;
  latency_ms: number | null;
  detail: string | null;
}

export interface ReadinessResponse {
  status: "ready" | "not_ready";
  checks: DependencyCheck[];
  timestamp: string;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  details?: unknown;
  request_id?: string | null;
}

export interface ApiErrorResponse {
  error: ApiErrorBody;
}
