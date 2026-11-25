import { appConfig } from "@/services/config";
import type { ApiErrorResponse } from "@/types/api";

export type ApiErrorCode = "timeout" | "network_error" | "invalid_response" | "http_error" | (string & {});

/** Error raised for every failed API call: HTTP errors, network failures, timeouts and malformed bodies. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  readonly details?: unknown;
  readonly requestId?: string;

  constructor(message: string, options: { status: number; code: ApiErrorCode; details?: unknown; requestId?: string }) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.details = options.details;
    this.requestId = options.requestId;
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  /** HTTP statuses that resolve with the parsed body instead of throwing (e.g. 503 from a health check). */
  acceptStatuses?: readonly number[];
}

/** Supplies extra headers per request. Reserved for the authentication module (e.g. a bearer token). */
export type HeadersProvider = () => Record<string, string>;

export interface ApiClientOptions {
  baseUrl: string;
  timeoutMs: number;
  fetchImpl?: typeof fetch;
  headersProvider?: HeadersProvider;
}

function isErrorEnvelope(value: unknown): value is ApiErrorResponse {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error = (value as { error: unknown }).error;
  return typeof error === "object" && error !== null && typeof (error as { message?: unknown }).message === "string";
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;
  private readonly headersProvider?: HeadersProvider;

  constructor({ baseUrl, timeoutMs, fetchImpl, headersProvider }: ApiClientOptions) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.timeoutMs = timeoutMs;
    this.fetchImpl = fetchImpl ?? ((...args) => globalThis.fetch(...args));
    this.headersProvider = headersProvider;
  }

  get<T>(path: string, options: Omit<RequestOptions, "method" | "body"> = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: "GET" });
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const { method = "GET", body, signal, acceptStatuses = [] } = options;
    const timeoutSignal = AbortSignal.timeout(this.timeoutMs);
    const combinedSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal;

    let response: Response;
    try {
      response = await this.fetchImpl(this.url(path), {
        method,
        headers: {
          Accept: "application/json",
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
          ...this.headersProvider?.(),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: combinedSignal,
      });
    } catch (error) {
      if (timeoutSignal.aborted) {
        throw new ApiError(`The request timed out after ${this.timeoutMs} ms.`, { status: 0, code: "timeout" });
      }
      if (signal?.aborted) throw error; // caller cancelled (e.g. query unmounted): not an API failure
      throw new ApiError("Unable to reach the OpsPilot AI server.", { status: 0, code: "network_error" });
    }

    const requestId = response.headers.get("X-Request-ID") ?? undefined;
    const payload = await this.parseBody(response);

    if (response.ok || acceptStatuses.includes(response.status)) {
      return payload as T;
    }
    if (isErrorEnvelope(payload)) {
      throw new ApiError(payload.error.message, {
        status: response.status,
        code: payload.error.code,
        details: payload.error.details,
        requestId: payload.error.request_id ?? requestId,
      });
    }
    throw new ApiError(`The request failed with status ${response.status}.`, {
      status: response.status,
      code: "http_error",
      requestId,
    });
  }

  private url(path: string): string {
    return `${this.baseUrl}/${path.replace(/^\/+/, "")}`;
  }

  private async parseBody(response: Response): Promise<unknown> {
    if (response.status === 204) return undefined;
    const contentType = response.headers.get("Content-Type") ?? "";
    if (!contentType.includes("application/json")) return undefined;
    try {
      return await response.json();
    } catch {
      throw new ApiError("The server returned a malformed response.", {
        status: response.status,
        code: "invalid_response",
      });
    }
  }
}

export const apiClient = new ApiClient({ baseUrl: appConfig.apiBaseUrl, timeoutMs: appConfig.apiTimeoutMs });
