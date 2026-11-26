import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { navigation, visibleNavigation, type NavSection } from "@/app/navigation";
import { jsonResponse, makeAdmin, makeCurrentUser, makeUser, renderRoute, routeFetch } from "@/test/utils";

const admin = makeAdmin();
const grace = makeUser({ id: "22222222-2222-4222-8222-222222222222", email: "grace@example.com", full_name: "Grace Hopper" });

function adminFetch() {
  return routeFetch({
    "GET /health": () => new Promise(() => {}),
    "GET /health/database": () => new Promise(() => {}),
    "GET /api/v1/users": () => jsonResponse({ items: [grace], total: 1, offset: 0, limit: 20 }),
    [`GET /api/v1/users/${grace.id}`]: () => jsonResponse({ ...grace, active_session_count: 0 }),
    [`GET /api/v1/users/${grace.id}/sessions`]: () => jsonResponse([]),
    [`GET /api/v1/users/${grace.id}/roles`]: () => jsonResponse([]),
    [`GET /api/v1/users/${grace.id}/permissions`]: () => jsonResponse([]),
    [`GET /api/v1/users/${grace.id}/effective-permissions`]: () => new Promise(() => {}),
    "POST /api/v1/auth/logout": () => new Response(null, { status: 204 }),
  });
}

afterEach(() => {
  window.localStorage.clear();
});

describe("navigation registry", () => {
  const sections: NavSection[] = [
    { id: "a", items: [{ id: "home", label: "Home", to: "/", icon: navigation[0]!.items[0]!.icon }] },
    {
      id: "b",
      label: "Admin",
      items: [
        { id: "x", label: "X", to: "/x", icon: navigation[0]!.items[0]!.icon, permission: "x:read" },
        { id: "y", label: "Y", to: "/y", icon: navigation[0]!.items[0]!.icon, anyPermission: ["y:read", "y:manage"] },
      ],
    },
  ];

  it("filters items by permission key and drops empty sections", () => {
    expect(visibleNavigation(sections, () => false).map((s) => s.id)).toEqual(["a"]);
    const some = visibleNavigation(sections, (key) => key === "y:manage");
    expect(some[1]!.items.map((i) => i.id)).toEqual(["y"]);
  });

  it("never refers to role names", () => {
    const keys = navigation.flatMap((s) => s.items.flatMap((i) => [i.permission, ...(i.anyPermission ?? [])])).filter(Boolean);
    for (const key of keys) expect(key).toMatch(/^[a-z_]+:[a-z_]+$/);
  });
});

describe("application shell", () => {
  it("shows only the navigation an ordinary user may open", async () => {
    renderRoute("/", adminFetch(), makeCurrentUser());
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).queryByText("Administration")).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
  });

  it("shows administration navigation, the active item and breadcrumbs", async () => {
    renderRoute(`/admin/users/${grace.id}`, adminFetch(), admin);
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(within(nav).getByText("Administration")).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Users" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Roles" })).not.toHaveAttribute("aria-current");

    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByText("Administration").tagName).toBe("SPAN");
    expect(within(crumbs).getByRole("link", { name: "Users" })).toHaveAttribute("href", "/admin/users");
    expect(within(crumbs).getByText("User details")).toHaveAttribute("aria-current", "page");
  });

  it("navigates with the sidebar", async () => {
    const user = userEvent.setup();
    const { router } = renderRoute("/", adminFetch(), admin);
    const nav = await screen.findByRole("navigation", { name: "Main" });
    await user.click(within(nav).getByRole("link", { name: "Users" }));
    expect(router.state.location.pathname).toBe("/admin/users");
    expect(await screen.findByRole("heading", { level: 1, name: "Users" })).toBeInTheDocument();
  });

  it("collapses the sidebar to an icon rail and remembers it", async () => {
    const user = userEvent.setup();
    renderRoute("/", adminFetch(), admin);
    const collapse = await screen.findByRole("button", { name: "Collapse sidebar" });
    expect(collapse).toHaveAttribute("aria-expanded", "true");
    await user.click(collapse);

    const expand = screen.getByRole("button", { name: "Expand sidebar" });
    expect(expand).toHaveAttribute("aria-expanded", "false");
    expect(window.localStorage.getItem("opspilot.sidebar.collapsed")).toBe("true");
    // Labels stay available to assistive technology and appear as tooltips.
    const users = within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Users" });
    expect(within(users).getByText("Users").className).toContain("sr-only");
  });

  it("opens the mobile navigation drawer and closes it after choosing a page", async () => {
    const user = userEvent.setup();
    const { router } = renderRoute("/", adminFetch(), admin);
    const trigger = await screen.findByRole("button", { name: "Open navigation" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await user.click(trigger);

    const drawer = screen.getByRole("dialog", { name: "Navigation" });
    const drawerNav = within(drawer).getByRole("navigation", { name: "Main" });
    await user.click(within(drawerNav).getByRole("link", { name: "Roles" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Navigation" })).not.toBeInTheDocument());
    expect(router.state.location.pathname).toBe("/admin/roles");
  });

  it("closes the mobile drawer with Escape and returns focus to the trigger", async () => {
    const user = userEvent.setup();
    renderRoute("/", adminFetch(), admin);
    await user.click(await screen.findByRole("button", { name: "Open navigation" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Navigation" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open navigation" })).toHaveFocus();
  });

  it("offers the account page and a confirmed sign-out from the user menu", async () => {
    const user = userEvent.setup();
    const { router } = renderRoute("/", adminFetch(), admin);
    await user.click(await screen.findByRole("button", { name: "Account menu" }));
    const menu = screen.getByRole("menu");
    expect(within(menu).getByText("admin@example.com")).toBeInTheDocument();
    await user.click(within(menu).getByRole("menuitem", { name: "Sign out" }));

    const confirm = screen.getByRole("alertdialog", { name: "Sign out?" });
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(router.state.location.pathname).toBe("/");

    await user.click(screen.getByRole("button", { name: "Account menu" }));
    await user.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: "Your account" }));
    expect(router.state.location.pathname).toBe("/account");
  });

  it("has a skip link to the main content", async () => {
    renderRoute("/", adminFetch(), admin);
    expect(await screen.findByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main-content");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
  });
});

describe("administration layout", () => {
  it("sends /admin to the first administration page the user may open", async () => {
    const roleReader = makeCurrentUser({ permissions: ["roles:read"] });
    const fetchImpl = routeFetch({ "GET /api/v1/roles": () => jsonResponse({ items: [], total: 0, offset: 0, limit: 20 }) });
    const { router } = renderRoute("/admin", fetchImpl, roleReader);
    await waitFor(() => expect(router.state.location.pathname).toBe("/admin/roles"));
  });

  it("shows 403 for /admin without any administration permission", async () => {
    renderRoute("/admin", adminFetch(), makeCurrentUser());
    expect(await screen.findByRole("heading", { name: "Not available" })).toBeInTheDocument();
  });
});
