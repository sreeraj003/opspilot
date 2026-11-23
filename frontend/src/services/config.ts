/** Environment-based runtime configuration, validated once at startup. */

export interface AppConfig {
  apiBaseUrl: string;
  apiTimeoutMs: number;
}

const DEFAULT_API_BASE_URL = "http://localhost:8000/api/v1";
const DEFAULT_TIMEOUT_MS = 10_000;

export function parseConfig(env: Record<string, string | undefined>): AppConfig {
  const rawBaseUrl = env.VITE_API_BASE_URL?.trim() || DEFAULT_API_BASE_URL;
  let apiBaseUrl: string;
  try {
    // Relative paths (e.g. "/api/v1" behind a reverse proxy) resolve against the page origin.
    const url = new URL(rawBaseUrl, globalThis.location?.origin ?? "http://localhost");
    apiBaseUrl = rawBaseUrl.startsWith("/") ? url.pathname : url.toString();
  } catch {
    throw new Error(`Invalid VITE_API_BASE_URL: "${rawBaseUrl}"`);
  }

  const rawTimeout = env.VITE_API_TIMEOUT_MS?.trim();
  const apiTimeoutMs = rawTimeout ? Number(rawTimeout) : DEFAULT_TIMEOUT_MS;
  if (!Number.isInteger(apiTimeoutMs) || apiTimeoutMs <= 0) {
    throw new Error(`Invalid VITE_API_TIMEOUT_MS: "${rawTimeout}"`);
  }

  return { apiBaseUrl: apiBaseUrl.replace(/\/+$/, ""), apiTimeoutMs };
}

export const appConfig: AppConfig = parseConfig(import.meta.env);
