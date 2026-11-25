import type { ApiClient } from "@/services/apiClient";
import type {
  CatalogParams,
  EffectivePermissions,
  Page,
  PermissionDefinition,
  PermissionDetail,
  PermissionInput,
  PermissionUpdateInput,
  Role,
  RoleDetail,
  RoleInput,
  UserPermissionAssignment,
  UserRoleAssignment,
} from "@/types/rbac";

/** Typed calls to the Module 04 endpoints. The server authorizes every one of them. */

const enc = encodeURIComponent;

function catalogQuery(params: CatalogParams): string {
  const query = new URLSearchParams({ offset: String(params.offset), limit: String(params.limit) });
  if (params.search) query.set("search", params.search);
  if (params.status && params.status !== "all") query.set("status", params.status);
  if (params.resource_type) query.set("resource_type", params.resource_type);
  return query.toString();
}

// --- roles ---------------------------------------------------------------------------------------

export function listRoles(client: ApiClient, params: CatalogParams, signal?: AbortSignal): Promise<Page<Role>> {
  return client.get<Page<Role>>(`/api/v1/roles?${catalogQuery(params)}`, { signal });
}

export function getRole(client: ApiClient, id: string, signal?: AbortSignal): Promise<RoleDetail> {
  return client.get<RoleDetail>(`/api/v1/roles/${enc(id)}`, { signal });
}

export function createRole(client: ApiClient, input: RoleInput): Promise<Role> {
  return client.post<Role>("/api/v1/roles", input);
}

export function updateRole(client: ApiClient, id: string, input: Partial<RoleInput>): Promise<Role> {
  return client.patch<Role>(`/api/v1/roles/${enc(id)}`, input);
}

export function deleteRole(client: ApiClient, id: string): Promise<void> {
  return client.delete<void>(`/api/v1/roles/${enc(id)}`);
}

export function setRolePermissions(
  client: ApiClient,
  id: string,
  permissionIds: string[],
): Promise<{ added: number; removed: number }> {
  return client.request(`/api/v1/roles/${enc(id)}/permissions`, { method: "PUT", body: { permission_ids: permissionIds } });
}

// --- permissions --------------------------------------------------------------------------------------

export function listPermissions(
  client: ApiClient,
  params: CatalogParams,
  signal?: AbortSignal,
): Promise<Page<PermissionDefinition>> {
  return client.get<Page<PermissionDefinition>>(`/api/v1/permissions?${catalogQuery(params)}`, { signal });
}

export function getPermission(client: ApiClient, id: string, signal?: AbortSignal): Promise<PermissionDetail> {
  return client.get<PermissionDetail>(`/api/v1/permissions/${enc(id)}`, { signal });
}

export function createPermission(client: ApiClient, input: PermissionInput): Promise<PermissionDefinition> {
  return client.post<PermissionDefinition>("/api/v1/permissions", input);
}

export function updatePermission(client: ApiClient, id: string, input: PermissionUpdateInput): Promise<PermissionDefinition> {
  return client.patch<PermissionDefinition>(`/api/v1/permissions/${enc(id)}`, input);
}

export function deletePermission(client: ApiClient, id: string): Promise<void> {
  return client.delete<void>(`/api/v1/permissions/${enc(id)}`);
}

export function validatePolicy(
  client: ApiClient,
  resourceType: string,
  policy: Record<string, unknown>,
): Promise<{ valid: boolean; policy_definition: Record<string, unknown> }> {
  return client.post("/api/v1/permissions/validate-policy", { resource_type: resourceType, policy_definition: policy });
}

// --- user access ----------------------------------------------------------------------------------------

export function listUserRoles(client: ApiClient, userId: string, signal?: AbortSignal): Promise<UserRoleAssignment[]> {
  return client.get<UserRoleAssignment[]>(`/api/v1/users/${enc(userId)}/roles`, { signal });
}

export function assignUserRole(
  client: ApiClient,
  userId: string,
  roleId: string,
  expiresAt?: string | null,
): Promise<UserRoleAssignment> {
  return client.post<UserRoleAssignment>(`/api/v1/users/${enc(userId)}/roles`, { role_id: roleId, expires_at: expiresAt ?? null });
}

export function revokeUserRole(client: ApiClient, userId: string, roleId: string): Promise<void> {
  return client.delete<void>(`/api/v1/users/${enc(userId)}/roles/${enc(roleId)}`);
}

export function listUserPermissions(
  client: ApiClient,
  userId: string,
  signal?: AbortSignal,
): Promise<UserPermissionAssignment[]> {
  return client.get<UserPermissionAssignment[]>(`/api/v1/users/${enc(userId)}/permissions`, { signal });
}

export function assignUserPermission(
  client: ApiClient,
  userId: string,
  input: { permission_id: string; assignment_type: "grant" | "deny"; expires_at?: string | null },
): Promise<UserPermissionAssignment> {
  return client.post<UserPermissionAssignment>(`/api/v1/users/${enc(userId)}/permissions`, input);
}

export function revokeUserPermission(client: ApiClient, userId: string, permissionId: string): Promise<void> {
  return client.delete<void>(`/api/v1/users/${enc(userId)}/permissions/${enc(permissionId)}`);
}

export function getEffectivePermissions(client: ApiClient, userId: string, signal?: AbortSignal): Promise<EffectivePermissions> {
  return client.get<EffectivePermissions>(`/api/v1/users/${enc(userId)}/effective-permissions`, { signal });
}

export function getMyPermissions(client: ApiClient, signal?: AbortSignal): Promise<EffectivePermissions> {
  return client.get<EffectivePermissions>("/api/v1/authorization/me", { signal });
}
