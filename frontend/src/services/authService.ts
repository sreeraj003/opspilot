import type { ApiClient } from "@/services/apiClient";
import type { CurrentUser, MessageResponse, TokenResponse } from "@/types/auth";

/**
 * Typed calls to /api/v1/auth. The refresh token never passes through this
 * code: the browser stores and sends it as an HttpOnly cookie.
 */

export const AUTH_BASE = "/api/v1/auth";
/** Required by the backend on cookie-authenticated endpoints (CSRF defence). */
export const CSRF_HEADERS = { "X-OpsPilot-CSRF": "1" } as const;

export function login(client: ApiClient, email: string, password: string): Promise<TokenResponse> {
  return client.post<TokenResponse>(`${AUTH_BASE}/login`, { email, password }, { anonymous: true, withCredentials: true });
}

export function refreshSession(client: ApiClient): Promise<TokenResponse> {
  return client.post<TokenResponse>(`${AUTH_BASE}/refresh`, undefined, {
    anonymous: true,
    withCredentials: true,
    headers: CSRF_HEADERS,
  });
}

export function logout(client: ApiClient): Promise<void> {
  return client.post<void>(`${AUTH_BASE}/logout`, undefined, {
    anonymous: true,
    withCredentials: true,
    headers: CSRF_HEADERS,
  });
}

export function logoutAll(client: ApiClient): Promise<void> {
  return client.post<void>(`${AUTH_BASE}/logout-all`, undefined, { withCredentials: true });
}

export function fetchCurrentUser(client: ApiClient, signal?: AbortSignal): Promise<CurrentUser> {
  return client.get<CurrentUser>(`${AUTH_BASE}/me`, { signal });
}

export function changePassword(client: ApiClient, currentPassword: string, newPassword: string): Promise<TokenResponse> {
  return client.post<TokenResponse>(
    `${AUTH_BASE}/change-password`,
    { current_password: currentPassword, new_password: newPassword },
    { withCredentials: true },
  );
}

export function requestPasswordReset(client: ApiClient, email: string): Promise<MessageResponse> {
  return client.post<MessageResponse>(`${AUTH_BASE}/forgot-password`, { email }, { anonymous: true });
}

export function resetPassword(client: ApiClient, token: string, newPassword: string): Promise<MessageResponse> {
  return client.post<MessageResponse>(
    `${AUTH_BASE}/reset-password`,
    { token, new_password: newPassword },
    { anonymous: true },
  );
}
