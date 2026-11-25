import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { permissionKeys } from "@/features/permissions/PermissionsProvider";
import { userKeys } from "@/features/users/hooks";
import { useApiClient } from "@/hooks/useApiClient";
import * as rbac from "@/services/rbacService";
import type { CatalogParams, PermissionInput, PermissionUpdateInput, RoleInput } from "@/types/rbac";

export const rbacKeys = {
  all: ["rbac"] as const,
  roles: (params: CatalogParams) => ["rbac", "roles", params] as const,
  role: (id: string) => ["rbac", "role", id] as const,
  permissions: (params: CatalogParams) => ["rbac", "permissions", params] as const,
  permission: (id: string) => ["rbac", "permission", id] as const,
  userRoles: (userId: string) => ["rbac", "user-roles", userId] as const,
  userPermissions: (userId: string) => ["rbac", "user-permissions", userId] as const,
  effective: (userId: string) => ["rbac", "effective", userId] as const,
};

export function useRoleList(params: CatalogParams, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: rbacKeys.roles(params),
    queryFn: ({ signal }) => rbac.listRoles(client, params, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useRole(id: string) {
  const client = useApiClient();
  return useQuery({ queryKey: rbacKeys.role(id), queryFn: ({ signal }) => rbac.getRole(client, id, signal) });
}

export function usePermissionList(params: CatalogParams, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: rbacKeys.permissions(params),
    queryFn: ({ signal }) => rbac.listPermissions(client, params, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function usePermission(id: string, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: rbacKeys.permission(id),
    queryFn: ({ signal }) => rbac.getPermission(client, id, signal),
    enabled,
  });
}

export function useUserRoles(userId: string, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: rbacKeys.userRoles(userId),
    queryFn: ({ signal }) => rbac.listUserRoles(client, userId, signal),
    enabled,
  });
}

export function useUserPermissions(userId: string, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: rbacKeys.userPermissions(userId),
    queryFn: ({ signal }) => rbac.listUserPermissions(client, userId, signal),
    enabled,
  });
}

export function useEffectivePermissions(userId: string, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: rbacKeys.effective(userId),
    queryFn: ({ signal }) => rbac.getEffectivePermissions(client, userId, signal),
    enabled,
  });
}

/**
 * Every access-control change refreshes all cached RBAC data, the user lists
 * (role counts) and the signed-in user's own permissions, so no screen keeps
 * showing access that was just changed.
 */
function useRbacMutation<TVariables, TResult>(fn: (variables: TVariables) => Promise<TResult>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: async () => {
      // The caller's own permissions and the user lists refresh in the background;
      // the RBAC screens are refetched before the mutation reports success.
      void queryClient.invalidateQueries({ queryKey: permissionKeys.all });
      void queryClient.invalidateQueries({ queryKey: userKeys.all });
      await queryClient.invalidateQueries({ queryKey: rbacKeys.all });
    },
  });
}

export function useCreateRole() {
  const client = useApiClient();
  return useRbacMutation((input: RoleInput) => rbac.createRole(client, input));
}

export function useUpdateRole(id: string) {
  const client = useApiClient();
  return useRbacMutation((input: Partial<RoleInput>) => rbac.updateRole(client, id, input));
}

export function useSetRoleActive() {
  const client = useApiClient();
  return useRbacMutation(({ id, active }: { id: string; active: boolean }) => rbac.updateRole(client, id, { is_active: active }));
}

export function useDeleteRole() {
  const client = useApiClient();
  return useRbacMutation((id: string) => rbac.deleteRole(client, id));
}

export function useSetRolePermissions(roleId: string) {
  const client = useApiClient();
  return useRbacMutation((permissionIds: string[]) => rbac.setRolePermissions(client, roleId, permissionIds));
}

export function useCreatePermission() {
  const client = useApiClient();
  return useRbacMutation((input: PermissionInput) => rbac.createPermission(client, input));
}

export function useUpdatePermission(id: string) {
  const client = useApiClient();
  return useRbacMutation((input: PermissionUpdateInput) => rbac.updatePermission(client, id, input));
}

export function useDeletePermission() {
  const client = useApiClient();
  return useRbacMutation((id: string) => rbac.deletePermission(client, id));
}

export function useAssignUserRole(userId: string) {
  const client = useApiClient();
  return useRbacMutation(({ roleId, expiresAt }: { roleId: string; expiresAt?: string | null }) =>
    rbac.assignUserRole(client, userId, roleId, expiresAt),
  );
}

export function useRevokeUserRole(userId: string) {
  const client = useApiClient();
  return useRbacMutation((roleId: string) => rbac.revokeUserRole(client, userId, roleId));
}

export function useAssignUserPermission(userId: string) {
  const client = useApiClient();
  return useRbacMutation((input: { permission_id: string; assignment_type: "grant" | "deny"; expires_at?: string | null }) =>
    rbac.assignUserPermission(client, userId, input),
  );
}

export function useRevokeUserPermission(userId: string) {
  const client = useApiClient();
  return useRbacMutation((permissionId: string) => rbac.revokeUserPermission(client, userId, permissionId));
}

/** Server-side validation and normalization of a policy, without saving anything. */
export function useValidatePolicy() {
  const client = useApiClient();
  return useMutation({
    mutationFn: ({ resourceType, policy }: { resourceType: string; policy: Record<string, unknown> }) => rbac.validatePolicy(client, resourceType, policy),
  });
}
