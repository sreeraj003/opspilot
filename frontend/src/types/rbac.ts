/** Contracts mirrored from backend/app/schemas/rbac.py and the policy format (docs/PERMISSION_POLICY.md). */

export type StatusFilter = "all" | "active" | "inactive";

export interface RoleSummary {
  id: string;
  key: string;
  name: string;
  is_system: boolean;
  is_active: boolean;
}

export interface Role extends RoleSummary {
  description: string | null;
  user_count: number;
  permission_count: number;
  created_at: string;
  updated_at: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}

export interface AuditEvent {
  id: string;
  event_type: string;
  actor_user_id: string | null;
  target_user_id: string | null;
  role_id: string | null;
  permission_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface PermissionSummary {
  id: string;
  key: string;
  name: string;
  resource_type: string;
  action: string;
  is_system: boolean;
  is_active: boolean;
}

export interface PermissionDefinition extends PermissionSummary {
  description: string | null;
  policy_definition: Record<string, unknown>;
  expires_at: string | null;
  role_count: number;
  user_count: number;
  created_at: string;
  updated_at: string;
}

export interface PermissionDetail extends PermissionDefinition {
  roles: RoleSummary[];
  history: AuditEvent[];
}

export interface RolePermission extends PermissionSummary {
  description: string | null;
  assigned_at: string;
}

export interface RoleUser {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  assigned_at: string;
  expires_at: string | null;
}

export interface RoleDetail extends Role {
  permissions: RolePermission[];
  users: RoleUser[];
  history: AuditEvent[];
}

export interface CatalogParams {
  offset: number;
  limit: number;
  search?: string;
  status?: StatusFilter;
  resource_type?: string;
}

export interface RoleInput {
  key: string;
  name: string;
  description?: string | null;
  is_active?: boolean;
}

export interface PermissionInput {
  key: string;
  name: string;
  description?: string | null;
  resource_type: string;
  action: string;
  policy_definition: Record<string, unknown>;
  is_active?: boolean;
  expires_at?: string | null;
}

export type PermissionUpdateInput = Partial<Omit<PermissionInput, "key">>;

export interface UserRoleAssignment {
  role: RoleSummary;
  assigned_at: string;
  assigned_by: string | null;
  expires_at: string | null;
  is_expired: boolean;
}

export interface UserPermissionAssignment {
  permission: PermissionSummary;
  assignment_type: "grant" | "deny";
  policy_override: Record<string, unknown> | null;
  assigned_at: string;
  assigned_by: string | null;
  expires_at: string | null;
}

export interface EffectiveGrant {
  key: string;
  name: string;
  resource_type: string;
  action: string;
  effect: "allow" | "deny";
  source: "role" | "direct";
  role_key: string | null;
  expires_at: string | null;
}

export interface TableAccess {
  table: string;
  columns: string[];
  denied_columns: string[];
  operations: string[];
  row_filter: FilterNode | null;
  allow_export: boolean;
  allow_sql_view: boolean;
}

export interface EffectivePermissions {
  user_id: string;
  policy_revision: number;
  evaluated_at: string;
  is_active: boolean;
  is_administrator: boolean;
  roles: { id: string; key: string; name: string; expires_at: string | null }[];
  system_permissions: string[];
  permission_keys: string[];
  revoked_permission_keys: string[];
  grants: EffectiveGrant[];
  restrictions: EffectiveGrant[];
  data_access: { action: string; tables: TableAccess[] }[];
  resources: { resource_type: string; action: string; allow_export: boolean; allow_sql_view: boolean }[];
}

// --- policy format -------------------------------------------------------------------------------

export const OPERATORS = [
  "equals",
  "not_equals",
  "in",
  "not_in",
  "greater_than",
  "greater_than_or_equal",
  "less_than",
  "less_than_or_equal",
  "between",
  "is_null",
  "is_not_null",
] as const;
export type Operator = (typeof OPERATORS)[number];

export const VALUE_TYPES = ["string", "number", "boolean", "date", "datetime"] as const;
export type ValueType = (typeof VALUE_TYPES)[number];

export interface Condition {
  column: string;
  operator: Operator;
  value?: unknown;
  value_type?: ValueType;
}

export interface Group {
  logic: "and" | "or" | "not";
  conditions: FilterNode[];
}

export type FilterNode = Condition | Group;

export function isGroup(node: FilterNode): node is Group {
  return "conditions" in node;
}
