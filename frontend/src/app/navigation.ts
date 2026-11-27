import { BookOpenText, BrainCircuit, Cable, Database, Home, KeyRound, MessageSquareText, ShieldCheck, Users, type LucideIcon } from "lucide-react";

/**
 * The application's navigation registry. The sidebar, the mobile drawer and the
 * administration gate are all built from this list, so a new module adds its
 * pages by adding an item here (and a route in routes.tsx); nothing else changes.
 *
 * ``permission`` / ``anyPermission`` decide only what is shown. They are UI hints:
 * the backend authorizes every request regardless of what the sidebar displays.
 * Use permission keys, never role names.
 */
export interface NavItem {
  id: string;
  label: string;
  to: string;
  icon: LucideIcon;
  /** Shown only to users who hold this permission key. */
  permission?: string;
  /** Shown to users who hold at least one of these keys. */
  anyPermission?: string[];
  /** Active only on an exact path match (for "/"). */
  end?: boolean;
}

export interface NavSection {
  id: string;
  /** Section heading; omitted for the first, unlabelled group. */
  label?: string;
  items: NavItem[];
}

export const navigation: NavSection[] = [
  {
    id: "workspace",
    items: [{ id: "home", label: "Home", to: "/", icon: Home, end: true }],
  },
  {
    id: "administration",
    label: "Administration",
    items: [
      { id: "users", label: "Users", to: "/admin/users", icon: Users, permission: "users:read" },
      { id: "roles", label: "Roles", to: "/admin/roles", icon: ShieldCheck, permission: "roles:read" },
      { id: "permissions", label: "Permissions", to: "/admin/permissions", icon: KeyRound, permission: "permissions:read" },
      { id: "schemas", label: "Schemas", to: "/admin/schemas", icon: Database, permission: "schema.read" },
      { id: "connectors", label: "Connectors", to: "/admin/connectors", icon: Cable, permission: "connector.read" },
      {
        id: "ai",
        label: "AI provider",
        to: "/admin/ai",
        icon: BrainCircuit,
        anyPermission: ["ai.provider.read", "ai.provider.test", "ai.usage.read"],
      },
      { id: "business-terms", label: "Business terms", to: "/admin/business-terms", icon: BookOpenText, permission: "business_term.read" },
      {
        id: "question-preview",
        label: "Question preview",
        to: "/admin/question-preview",
        icon: MessageSquareText,
        permission: "ai.question.preview",
      },
    ],
  },
];

export function canSeeItem(item: NavItem, hasPermission: (key: string) => boolean): boolean {
  if (item.permission && !hasPermission(item.permission)) return false;
  if (item.anyPermission && !item.anyPermission.some(hasPermission)) return false;
  return true;
}

/** The sections and items this user may see; empty sections are dropped. */
export function visibleNavigation(sections: NavSection[], hasPermission: (key: string) => boolean): NavSection[] {
  return sections
    .map((section) => ({ ...section, items: section.items.filter((item) => canSeeItem(item, hasPermission)) }))
    .filter((section) => section.items.length > 0);
}

/** Items of one section, for gates such as the administration layout. */
export function sectionItems(id: string): NavItem[] {
  return navigation.find((section) => section.id === id)?.items ?? [];
}
