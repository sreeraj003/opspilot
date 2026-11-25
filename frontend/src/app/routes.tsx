import type { RouteObject } from "react-router";

import { ErrorFallback } from "@/components/ErrorBoundary";
import { AppShell } from "@/layouts/AppShell";
import { HomePage } from "@/pages/HomePage";
import { NotFoundPage } from "@/pages/NotFoundPage";

function RouteErrorFallback() {
  return <ErrorFallback error={new Error("This page failed to load.")} onReset={() => window.location.reload()} />;
}

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <AppShell />,
    errorElement: <RouteErrorFallback />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
