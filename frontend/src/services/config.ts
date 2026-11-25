/** Environment-based runtime configuration, validated once at startup. */

export interface AppConfig {
  apiBaseUrl: string;
  apiTimeoutMs: number;
}

const DEFAULT_API_BASE_URL = "http://localhost:8000";
const DEFAULT_TIMEOUT_MS = 10_000;

export function parseConfig(env: Record<string, string | undefined>): AppConfig {
  const rawBaseUrl = env.VITE_API_BASE_URL?.trim() || DEFAULT_API_BASE_URL;
  let apiBaseUrl: string;
  if (rawBaseUrl.startsWith("/")) {
    // A relative path (for example "/api" behind a reverse proxy) resolves against the page origin.
    apiBaseUrl = rawBaseUrl;
  } else {
    try {
      const url = new URL(rawBaseUrl);
      if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error("unsupported protocol");
      apiBaseUrl = url.toString();
    } catch {
      throw new Error(`Invalid VITE_API_BASE_URL: "${rawBaseUrl}"`);
    }
  }

  const rawTimeout = env.VITE_API_TIMEOUT_MS?.trim();
  const apiTimeoutMs = rawTimeout ? Number(rawTimeout) : DEFAULT_TIMEOUT_MS;
  if (!Number.isInteger(apiTimeoutMs) || apiTimeoutMs <= 0) {
    throw new Error(`Invalid VITE_API_TIMEOUT_MS: "${rawTimeout}"`);
  }

  return { apiBaseUrl: apiBaseUrl.replace(/\/+$/, ""), apiTimeoutMs };
}

export const appConfig: AppConfig = parseConfig(import.meta.env);
