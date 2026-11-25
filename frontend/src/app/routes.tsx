import type { RouteObject } from "react-router";

import { ErrorFallback } from "@/components/ErrorBoundary";
import { RedirectIfAuthenticated, RequireAuth, RequirePermission } from "@/features/auth/guards";
import { AppShell } from "@/layouts/AppShell";
import { AccountPage } from "@/pages/AccountPage";
import { ForgotPasswordPage } from "@/pages/ForgotPasswordPage";
import { HomePage } from "@/pages/HomePage";
import { LoginPage } from "@/pages/LoginPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ResetPasswordPage } from "@/pages/ResetPasswordPage";
import { UserDetailPage } from "@/pages/UserDetailPage";
import { UsersPage } from "@/pages/UsersPage";

function RouteErrorFallback() {
  return <ErrorFallback error={new Error("This page failed to load.")} onReset={() => window.location.reload()} />;
}

export const routes: RouteObject[] = [
  {
    errorElement: <RouteErrorFallback />,
    children: [
      {
        element: <RedirectIfAuthenticated />,
        children: [
          { path: "/login", element: <LoginPage /> },
          { path: "/forgot-password", element: <ForgotPasswordPage /> },
        ],
      },
      // Reachable whether or not someone is signed in (the link may be opened in any browser).
      { path: "/reset-password", element: <ResetPasswordPage /> },
      {
        element: <RequireAuth />,
        children: [
          {
            path: "/",
            element: <AppShell />,
            children: [
              { index: true, element: <HomePage /> },
              { path: "account", element: <AccountPage /> },
              {
                path: "admin/users",
                element: <RequirePermission permission="users:read" />,
                children: [
                  { index: true, element: <UsersPage /> },
                  { path: ":userId", element: <UserDetailPage /> },
                ],
              },
              { path: "*", element: <NotFoundPage /> },
            ],
          },
        ],
      },
    ],
  },
];
