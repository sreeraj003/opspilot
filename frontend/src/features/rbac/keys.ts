/** Identifier rules shared by the role and permission forms (mirrors backend/app/models/rbac.py). */

export const KEY_PATTERN = /^[a-z][a-z0-9_]*([.:-][a-z0-9_]+)*$/;

/** Granted only through the built-in Administrator role, never to other roles or directly. */
export const ADMINISTRATOR_ONLY = new Set(["users:manage", "settings:manage", "roles:manage", "permissions:manage", "access:assign"]);

/** A key suggestion derived from a display name: "Regional Manager" -> "regional_manager". */
export function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/^[^a-z]+/, "")
    .slice(0, 100);
}
