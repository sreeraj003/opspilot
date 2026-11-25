import type { ApiClient } from "@/services/apiClient";
import type {
  AdminPasswordResetResponse,
  User,
  UserCreateInput,
  UserDetail,
  UserListParams,
  UserPage,
  UserSession,
  UserUpdateInput,
} from "@/types/auth";

/** Typed calls to /api/v1/users (administrators only; the server enforces it). */

const USERS = "/api/v1/users";

export function listUsers(client: ApiClient, params: UserListParams, signal?: AbortSignal): Promise<UserPage> {
  const query = new URLSearchParams({ offset: String(params.offset), limit: String(params.limit) });
  if (params.search) query.set("search", params.search);
  if (params.status && params.status !== "all") query.set("status", params.status);
  if (params.sort) query.set("sort", params.sort);
  if (params.order) query.set("order", params.order);
  return client.get<UserPage>(`${USERS}?${query.toString()}`, { signal });
}

export function getUser(client: ApiClient, id: string, signal?: AbortSignal): Promise<UserDetail> {
  return client.get<UserDetail>(`${USERS}/${encodeURIComponent(id)}`, { signal });
}

export function listUserSessions(client: ApiClient, id: string, signal?: AbortSignal): Promise<UserSession[]> {
  return client.get<UserSession[]>(`${USERS}/${encodeURIComponent(id)}/sessions`, { signal });
}

export function createUser(client: ApiClient, input: UserCreateInput): Promise<User> {
  return client.post<User>(USERS, input);
}

export function updateUser(client: ApiClient, id: string, input: UserUpdateInput): Promise<User> {
  return client.patch<User>(`${USERS}/${encodeURIComponent(id)}`, input);
}

export function activateUser(client: ApiClient, id: string): Promise<User> {
  return client.post<User>(`${USERS}/${encodeURIComponent(id)}/activate`);
}

export function deactivateUser(client: ApiClient, id: string): Promise<User> {
  return client.post<User>(`${USERS}/${encodeURIComponent(id)}/deactivate`);
}

export function revokeUserSessions(client: ApiClient, id: string): Promise<{ revoked_sessions: number }> {
  return client.post<{ revoked_sessions: number }>(`${USERS}/${encodeURIComponent(id)}/revoke-sessions`);
}

export function startPasswordReset(client: ApiClient, id: string): Promise<AdminPasswordResetResponse> {
  return client.post<AdminPasswordResetResponse>(`${USERS}/${encodeURIComponent(id)}/password-reset`);
}

export function deleteUser(client: ApiClient, id: string): Promise<void> {
  return client.delete<void>(`${USERS}/${encodeURIComponent(id)}`);
}
