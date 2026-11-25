import type { RouteObject } from "react-router";

import { PermissionEditorPage, PermissionsPage, RoleDetailPage, RolesPage, UserDetailPage, UsersPage } from "@/app/lazyPages";
import type { RouteHandle } from "@/app/routeHandle";
import { RouteErrorFallback } from "@/components/ErrorBoundary";
import { RedirectIfAuthenticated, RequireAuth, RequirePermission } from "@/features/auth/guards";
import { AdminIndexRedirect, AdminLayout } from "@/layouts/AdminLayout";
import { AppLayout } from "@/layouts/AppLayout";
import { AuthLayout } from "@/layouts/AuthLayout";
import { PublicLayout } from "@/layouts/PublicLayout";
import { AccountPage } from "@/pages/AccountPage";
import { ForgotPasswordPage } from "@/pages/ForgotPasswordPage";
import { HomePage } from "@/pages/HomePage";
import { LoginPage } from "@/pages/LoginPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ResetPasswordPage } from "@/pages/ResetPasswordPage";

const crumb = (label: RouteHandle["crumb"], link = true): RouteHandle => ({ crumb: label, link });

/**
 * Route table. Layout routes, from the outside in:
 * - AuthLayout (under RedirectIfAuthenticated): sign-in and forgotten password.
 * - PublicLayout: pages anyone can open (password reset links).
 * - AppLayout (under RequireAuth): the authenticated shell.
 * - AdminLayout: the /admin area; each page adds RequirePermission.
 * To add a page: add a route here (with a ``handle`` crumb) and, if it belongs in
 * the sidebar, an item in app/navigation.ts.
 */
export const routes: RouteObject[] = [
  {
    errorElement: <RouteErrorFallback />,
    children: [
      {
        element: <RedirectIfAuthenticated />,
        children: [
          {
            element: <AuthLayout />,
            children: [
              { path: "/login", element: <LoginPage /> },
              { path: "/forgot-password", element: <ForgotPasswordPage /> },
            ],
          },
        ],
      },
      {
        element: <PublicLayout />,
        // Reachable whether or not someone is signed in (the link may be opened in any browser).
        children: [{ path: "/reset-password", element: <ResetPasswordPage /> }],
      },
      {
        element: <RequireAuth />,
        children: [
          {
            path: "/",
            element: <AppLayout />,
            children: [
              { index: true, element: <HomePage />, handle: crumb("Home") },
              { path: "account", element: <AccountPage />, handle: crumb("Your account") },
              {
                path: "admin",
                element: <AdminLayout />,
                handle: crumb("Administration", false),
                children: [
                  { index: true, element: <AdminIndexRedirect /> },
                  {
                    path: "users",
                    element: <RequirePermission permission="users:read" />,
                    handle: crumb("Users"),
                    children: [
                      { index: true, element: <UsersPage /> },
                      { path: ":userId", element: <UserDetailPage />, handle: crumb("User details") },
                    ],
                  },
                  {
                    path: "roles",
                    element: <RequirePermission permission="roles:read" />,
                    handle: crumb("Roles"),
                    children: [
                      { index: true, element: <RolesPage /> },
                      { path: ":roleId", element: <RoleDetailPage />, handle: crumb("Role details") },
                    ],
                  },
                  {
                    path: "permissions",
                    element: <RequirePermission permission="permissions:read" />,
                    handle: crumb("Permissions"),
                    children: [
                      { index: true, element: <PermissionsPage /> },
                      {
                        path: ":permissionId",
                        element: <PermissionEditorPage />,
                        handle: crumb((params) => (params.permissionId === "new" ? "New permission" : "Permission details")),
                      },
                    ],
                  },
                ],
              },
              { path: "*", element: <NotFoundPage />, handle: crumb("Page not found") },
            ],
          },
        ],
      },
    ],
  },
];
