/** Contracts mirrored from backend/app/schemas/auth.py and user.py. */

export interface User {
  id: string;
  email: string;
  username: string | null;
  full_name: string;
  is_active: boolean;
  is_verified: boolean;
  is_superuser: boolean;
  is_locked: boolean;
  locked_until: string | null;
  last_login_at: string | null;
  created_at: string;
  updated_at: string;
}

/** Built-in system permissions checked by the API's routes. Administrators create other permission keys at run time. */
export type SystemPermission =
  | "users:read"
  | "users:manage"
  | "settings:read"
  | "settings:manage"
  | "roles:read"
  | "roles:manage"
  | "permissions:read"
  | "permissions:manage"
  | "access:review"
  | "access:assign"
  | "schema.read"
  | "schema.create"
  | "schema.update"
  | "schema.delete"
  | "schema.import"
  | "schema.export"
  | "schema.relationship.manage"
  | "connector.read"
  | "connector.create"
  | "connector.update"
  | "connector.delete"
  | "connector.test"
  | "connector.activate"
  | "ai.provider.read"
  | "ai.provider.test"
  | "ai.usage.read"
  | "ai.question.understand"
  | "ai.question.preview"
  | "business_term.read"
  | "business_term.manage";

export type Permission = SystemPermission | (string & {});

export interface CurrentUser extends User {
  /** For display decisions only. The server enforces every permission itself. */
  permissions: Permission[];
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: CurrentUser;
}

export interface MessageResponse {
  message: string;
}

export interface UserDetail extends User {
  active_session_count: number;
}

export interface UserSession {
  id: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string;
  user_agent: string | null;
  ip_address: string | null;
}

export interface UserPage {
  items: User[];
  total: number;
  offset: number;
  limit: number;
}

export type UserStatusFilter = "all" | "active" | "inactive" | "locked";
export type UserSortField = "created_at" | "email" | "full_name" | "last_login_at";

export interface UserListParams {
  offset: number;
  limit: number;
  search?: string;
  status?: UserStatusFilter;
  sort?: UserSortField;
  order?: "asc" | "desc";
}

export interface UserCreateInput {
  email: string;
  full_name: string;
  username?: string | null;
  password: string;
  is_active: boolean;
}

export interface UserUpdateInput {
  email?: string;
  full_name?: string;
  username?: string | null;
}

export interface AdminPasswordResetResponse {
  reset_url: string | null;
  expires_at: string;
}
