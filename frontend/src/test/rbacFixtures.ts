import type { EffectivePermissions, Page, PermissionDefinition, PermissionDetail, Role, RoleDetail } from "@/types/rbac";

export const ROLE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const PERMISSION_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

export function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, offset: 0, limit: 20 };
}

export function makeRole(overrides: Partial<Role> = {}): Role {
  return {
    id: ROLE_ID,
    key: "regional_manager",
    name: "Regional Manager",
    description: "Sales leads for one region",
    is_system: false,
    is_active: true,
    user_count: 2,
    permission_count: 1,
    created_at: "2026-11-20T08:00:00Z",
    updated_at: "2026-11-20T08:00:00Z",
    ...overrides,
  };
}

export function makePermission(overrides: Partial<PermissionDefinition> = {}): PermissionDefinition {
  return {
    id: PERMISSION_ID,
    key: "sales.karnataka.view",
    name: "View Karnataka sales",
    description: null,
    resource_type: "database_data",
    action: "view",
    is_system: false,
    is_active: true,
    policy_definition: {
      version: 1,
      effect: "allow",
      allowed_tables: ["sales"],
      allowed_columns: ["sales.*"],
      allowed_operations: ["aggregate", "read"],
      row_filters: [{ column: "sales.region", operator: "equals", value: "Karnataka" }],
      allow_export: false,
      allow_sql_view: false,
    },
    expires_at: null,
    role_count: 1,
    user_count: 0,
    created_at: "2026-11-20T08:00:00Z",
    updated_at: "2026-11-20T08:00:00Z",
    ...overrides,
  };
}

export function makePermissionDetail(overrides: Partial<PermissionDetail> = {}): PermissionDetail {
  return { ...makePermission(), roles: [], history: [], ...overrides };
}

export function makeRoleDetail(overrides: Partial<RoleDetail> = {}): RoleDetail {
  const permission = makePermission();
  return {
    ...makeRole(),
    permissions: [{ ...permission, assigned_at: "2026-11-20T08:00:00Z" }],
    users: [],
    history: [],
    ...overrides,
  };
}

export function makeEffective(overrides: Partial<EffectivePermissions> = {}): EffectivePermissions {
  return {
    user_id: "22222222-2222-4222-8222-222222222222",
    policy_revision: 7,
    evaluated_at: "2026-11-25T08:00:00Z",
    is_active: true,
    is_administrator: false,
    roles: [],
    system_permissions: [],
    permission_keys: [],
    revoked_permission_keys: [],
    grants: [],
    restrictions: [],
    data_access: [],
    resources: [],
    ...overrides,
  };
}
