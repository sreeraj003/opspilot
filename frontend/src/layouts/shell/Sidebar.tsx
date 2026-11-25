import { ChevronsLeft, ChevronsRight, LogOut } from "lucide-react";
import { NavLink } from "react-router";

import { visibleNavigation, navigation, type NavItem } from "@/app/navigation";
import { BrandMark } from "@/components/BrandMark";
import { Avatar } from "@/components/ui/Avatar";
import { Tooltip } from "@/components/ui/Tooltip";
import { useAuth } from "@/features/auth/authContext";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { cn } from "@/utils/cn";

export interface SidebarContentProps {
  /** Icon-only rail (desktop collapsed state). */
  collapsed?: boolean;
  /** Called after a link is chosen (closes the mobile drawer). */
  onNavigate?: () => void;
  onSignOut: () => void;
  /** Rendered only on desktop: the collapse/expand control. */
  onToggleCollapsed?: () => void;
  sidebarId?: string;
}

function NavEntry({ item, collapsed, onNavigate }: { item: NavItem; collapsed: boolean; onNavigate?: () => void }) {
  const Icon = item.icon;
  return (
    <Tooltip content={item.label} side="right" disabled={!collapsed} className="w-full">
      <NavLink
        to={item.to}
        end={item.end}
        onClick={onNavigate}
        className={({ isActive }) =>
          cn(
            "group relative flex h-9 w-full items-center gap-3 rounded-control px-2.5 text-sm font-medium transition-colors",
            collapsed && "justify-center px-0",
            isActive ? "bg-accent-subtle text-accent-text" : "text-fg-muted hover:bg-surface-muted hover:text-fg",
          )
        }
      >
        {({ isActive }) => (
          <>
            {isActive && <span aria-hidden="true" className="absolute top-1.5 bottom-1.5 -left-3 w-0.5 rounded-full bg-accent" />}
            <Icon aria-hidden="true" className="h-4 w-4 shrink-0" strokeWidth={isActive ? 2.25 : 1.9} />
            <span className={cn("truncate", collapsed && "sr-only")}>{item.label}</span>
          </>
        )}
      </NavLink>
    </Tooltip>
  );
}

/**
 * Sidebar body shared by the docked desktop sidebar and the mobile drawer.
 * Items come from the navigation registry, filtered by the user's effective permissions.
 */
export function SidebarContent({ collapsed = false, onNavigate, onSignOut, onToggleCollapsed, sidebarId }: SidebarContentProps) {
  const { user } = useAuth();
  const { hasPermission } = usePermissions();
  const sections = visibleNavigation(navigation, hasPermission);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className={cn("flex h-topbar shrink-0 items-center px-4", collapsed && "justify-center px-0")}>
        <BrandMark compact={collapsed} />
      </div>

      <nav aria-label="Main" id={sidebarId} className={cn("min-h-0 flex-1 overflow-y-auto px-3 py-3", collapsed && "px-2")}>
        {sections.map((section, index) => (
          <div key={section.id} className={cn(index > 0 && "mt-6")}>
            {section.label &&
              (collapsed ? (
                <div aria-hidden="true" className="mx-auto mb-2 h-px w-6 bg-line" />
              ) : (
                <p className="mb-1.5 px-2.5 text-2xs font-semibold tracking-wider text-fg-subtle uppercase">{section.label}</p>
              ))}
            <ul className="space-y-0.5" aria-label={collapsed ? section.label : undefined}>
              {section.items.map((item) => (
                <li key={item.id}>
                  <NavEntry item={item} collapsed={collapsed} onNavigate={onNavigate} />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className={cn("shrink-0 border-t border-line p-3", collapsed && "px-2")}>
        {user && (
          <div className={cn("flex items-center gap-2.5", collapsed && "flex-col")}>
            <NavLink
              to="/account"
              onClick={onNavigate}
              aria-label={collapsed ? `Your account (${user.full_name})` : undefined}
              className="flex min-w-0 flex-1 items-center gap-2.5 rounded-control p-1 hover:bg-surface-muted"
            >
              <Avatar name={user.full_name} />
              {!collapsed && (
                <span className="min-w-0 text-left">
                  <span className="block truncate text-sm font-medium text-fg">{user.full_name}</span>
                  <span className="block truncate text-xs text-fg-muted">{user.email}</span>
                </span>
              )}
            </NavLink>
            <Tooltip content="Sign out" side={collapsed ? "right" : "top"}>
              <button
                type="button"
                onClick={onSignOut}
                aria-label="Sign out"
                className="grid h-8 w-8 shrink-0 place-items-center rounded-control text-fg-subtle hover:bg-surface-muted hover:text-fg"
              >
                <LogOut aria-hidden="true" className="h-4 w-4" />
              </button>
            </Tooltip>
          </div>
        )}
        {onToggleCollapsed && (
          <button
            type="button"
            onClick={onToggleCollapsed}
            aria-controls={sidebarId}
            aria-expanded={!collapsed}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className={cn(
              "mt-2 flex h-8 w-full items-center gap-2 rounded-control px-2.5 text-xs font-medium text-fg-muted hover:bg-surface-muted hover:text-fg",
              collapsed && "justify-center px-0",
            )}
          >
            {collapsed ? <ChevronsRight aria-hidden="true" className="h-4 w-4" /> : <ChevronsLeft aria-hidden="true" className="h-4 w-4" />}
            {!collapsed && "Collapse"}
          </button>
        )}
      </div>
    </div>
  );
}
