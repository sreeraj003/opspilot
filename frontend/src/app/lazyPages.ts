import { lazy } from "react";

// Administration pages load on demand (AppLayout's <Suspense> shows a loading state), keeping the
// first download small for people who never open them.
export const UsersPage = lazy(() => import("@/pages/UsersPage").then((m) => ({ default: m.UsersPage })));
export const UserDetailPage = lazy(() => import("@/pages/UserDetailPage").then((m) => ({ default: m.UserDetailPage })));
export const RolesPage = lazy(() => import("@/pages/RolesPage").then((m) => ({ default: m.RolesPage })));
export const RoleDetailPage = lazy(() => import("@/pages/RoleDetailPage").then((m) => ({ default: m.RoleDetailPage })));
export const PermissionsPage = lazy(() => import("@/pages/PermissionsPage").then((m) => ({ default: m.PermissionsPage })));
export const SchemasPage = lazy(() => import("@/pages/SchemasPage").then((m) => ({ default: m.SchemasPage })));
export const SchemaDetailPage = lazy(() => import("@/pages/SchemaDetailPage").then((m) => ({ default: m.SchemaDetailPage })));
export const PermissionEditorPage = lazy(() => import("@/pages/PermissionEditorPage").then((m) => ({ default: m.PermissionEditorPage })));
export const ConnectorsPage = lazy(() => import("@/pages/ConnectorsPage").then((m) => ({ default: m.ConnectorsPage })));
export const ConnectorDetailPage = lazy(() => import("@/pages/ConnectorDetailPage").then((m) => ({ default: m.ConnectorDetailPage })));
