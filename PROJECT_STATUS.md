# OpsPilot AI: project status

## Current module

**Module 05: Frontend foundation and design system.** Implemented and verified on 2026-11-25. Awaiting the project
owner's review. Module 06 has not been started.

Single installation, single organisation: no tenant model. Modules 01 to 04 keep their behaviour: the frontend was
consolidated, not rebuilt, and no backend code changed. Earlier records are kept below.

## Module 05: what was implemented

Reference: [docs/FRONTEND_DESIGN_SYSTEM.md](docs/FRONTEND_DESIGN_SYSTEM.md) and ARCHITECTURE.md section 6.

- **Design tokens** (`src/styles/tokens.css`, mirrored in `src/design/tokens.ts`): semantic colours (neutrals,
  lines, text, a restrained indigo accent, success/warning/danger/info), typography, font sizes and weights, spacing
  names, radius, shadows, breakpoints, motion and z-index layers. Tailwind's default palette is removed so only
  tokens can be used; every existing page was migrated off raw `stone-*`/`rose-*`/`white` classes.
- **Light and dark themes** with a "system" option (`ThemeProvider`, `ThemeToggle`, Appearance tab), applied before
  first paint by `public/theme-init.js`. Reduced-motion support is global.
- **Component library** (`src/components/ui/`): Button (5 variants, 5 sizes, loading) and ButtonLink, Input,
  Textarea, Select, PasswordInput, SearchInput, Checkbox, Switch, FormField (+ TextField, PasswordField,
  TextareaField), Card, PageHeader, SectionHeader, Divider, Stack, ResponsiveContainer, Breadcrumbs, Tabs,
  DropdownMenu, Badge, StatusIndicator, Alert, Toast, Tooltip, LoadingSpinner/Spinner, Skeleton, EmptyState,
  ErrorState, Dialog (Modal) and ConfirmDialog, Drawer, Table primitives, DataTable, Pagination, Avatar. Earlier
  duplicates (StatusBadge, per-page tables, empty states, pagination and search boxes, RBAC field classes) now use
  these.
- **Application shell** (`layouts/AppLayout` + `layouts/shell/`): branded sidebar with sections, active indicator,
  collapse to an icon rail (remembered), user profile and sign-out; mobile navigation drawer; sticky top bar with
  page title / breadcrumbs, theme picker and account menu; skip link; content inside error and loading boundaries.
  No notification area (nothing produces notifications yet).
- **Layouts**: public (`/reset-password`), authentication (`/login`, `/forgot-password`, with a product panel on
  large screens), application, and protected administration (`/admin/*`, with `/admin` sending users to the first
  administration page they may open).
- **Navigation registry** (`src/app/navigation.ts`): sections and items with permission keys; sidebar, drawer and
  the administration gate are derived from it. Breadcrumbs come from route `handle.crumb`.
- **Permission-aware UI**: items and actions are shown from `usePermissions()` (Module 04's
  `/authorization/me`), never from role names.
- **Authentication pages** polished on the new layouts; a branded session-check screen; sign-out now asks for
  confirmation (sidebar and account menu). Token handling, refresh, expiry notice and redirect rules are unchanged.
- **User management**: list with search, status filter, sort, pagination, avatars, responsive columns, skeleton,
  empty and error states with retry; create dialog; **edit dialog** (new, replaces the inline form);
  activate/deactivate with confirmation and toasts; security actions; role assignment (Module 04's access section).
  Roles and permissions lists use the same DataTable.
- **Your account** page reorganised into Profile, Security and Appearance tabs.
- **Tooling**: ESLint 9 (typescript-eslint, react-hooks 7, jsx-a11y, react-refresh) added with `npm run lint`;
  administration pages are lazy-loaded and vendor code is split into chunks (no more chunk-size warning).

### New dependencies (frontend, dev only)

`eslint` 9.39.5, `@eslint/js` 9.39.5, `typescript-eslint` 8.74.0, `eslint-plugin-react-hooks` 7.1.1,
`eslint-plugin-react-refresh` 0.5.8, `eslint-plugin-jsx-a11y` 6.10.2, `globals` 17.14.0. No runtime dependencies were
added: React Hook Form and Zod were already present; Recharts is not needed until a module draws charts.

### Commands executed and results (Module 05)

| Command | Result |
| --- | --- |
| `npx vitest run` | **151 passed** in 25 files (93 before; 58 added, 4 updated for the new shell) |
| `npx tsc -b` | OK, no errors |
| `npm run lint` | OK, 0 errors, 0 warnings |
| `npm run build` | OK; entry 74 kB (21 kB gzip), react 315 kB (99 kB gzip), data 153 kB (46 kB gzip), admin pages 1 to 19 kB each; no chunk-size warning |
| Headless Chromium (Playwright) against uvicorn + Vite + PostgreSQL 16 on a fresh database | Signed in as the administrator; home, users, roles, permissions, user detail, edit dialog, create-user validation, deactivate with toast, collapsed sidebar, dark theme, account menu, appearance tab at 1440×900; home, drawer, users, create dialog (bottom sheet), user detail, permission editor, roles and login at 390×844. No console errors other than the expected 401 from the session check before sign-in; no horizontal page overflow (one found and fixed: sr-only table text widened the page on phones). |

Tests added: Button variants, sizes, loading and links; FormField ARIA wiring; password reveal; search clear;
checkbox and switch; React Hook Form + Zod validation and disabled submission; Dialog focus trap, Escape, backdrop
and focus return; ConfirmDialog; Drawer; DropdownMenu keyboard and dismissal; Tooltip; Alert, StatusIndicator,
Badge, Spinner, EmptyState, ErrorState; Toast live regions and timing; DataTable loading/empty/error/rows;
Pagination; Tabs; Avatar initials; navigation registry filtering; sidebar permission filtering, active item,
breadcrumbs, navigation, collapse persistence, mobile drawer open/close/Escape; account menu and confirmed sign-out;
skip link; administration redirect and 403; theme default, switching, persistence and invalid values;
authentication, public and session-check layouts; user edit dialog and validation; deactivate toast.

Not run in Module 05: backend tests (no backend file changed; Module 04's results stand), a native Windows run,
Docker Compose, real screen readers, and cross-browser checks beyond Chromium.

### Known limitations

- **The interface only decides what to show.** Hidden buttons and navigation are not authorization; the backend
  enforces every permission.
- **Notifications**: no notification area, because nothing produces notifications yet.
- **Theme preference** is per browser (localStorage), not stored on the account.
- **Fonts**: the Inter stack falls back to system fonts; no web font is bundled (no external requests).
- **Tooltips and menus** are positioned with CSS only (no collision detection); they are placed where they fit in
  the current layouts.
- **Narrow phones**: wide tables scroll horizontally inside their card; secondary columns are hidden below set
  breakpoints, and full details are on each record's page.
- **Accessibility** was checked with automated tests, jsx-a11y lint rules, keyboard walkthroughs in tests and a
  contrast calculation of the token pairs, not with an external audit or screen-reader testing.
- The UserAccessSection and policy builder (Module 04) were restyled through the tokens but keep their native
  inline selects.

## Module 04 record

### What was implemented

Backend
- **Models** (`app/models/rbac.py`): `roles`, `permissions` (structured JSONB `policy_definition`, `resource_type`,
  `action`, optional `expires_at`), `role_permissions`, `user_roles` (optional expiry), `user_permissions` (direct
  grant or deny, optional narrowing override, optional expiry), `rbac_audit_events`, `authorization_state`
  (policy revision). Soft deletes and revocation timestamps keep history; keys are unique among live rows.
- **Policy format and validator** (`app/authorization/policy.py`, [docs/PERMISSION_POLICY.md](docs/PERMISSION_POLICY.md)):
  versioned JSON, never code or SQL. Operators `equals`, `not_equals`, `in`, `not_in`, `greater_than`,
  `greater_than_or_equal`, `less_than`, `less_than_or_equal`, `between`, `is_null`, `is_not_null`; value types
  string, number, boolean, date, datetime; nested AND/OR groups with depth and size limits; detection of
  contradictions and duplicates; normalized storage. Schema-aware checks are behind a `SchemaCatalog` protocol
  (no schemas exist yet, so they are skipped).
- **Engine** (`app/authorization/engine.py`): deny by default; inactive users denied; expired, revoked and inactive
  grants ignored; direct denies remove permissions; deny policies always win; row-level and column-level
  restrictions returned as structured decisions, never SQL. Three queries resolve any number of users.
- **Cache** (`app/authorization/cache.py`): bounded per-process LRU validated against the database revision, which
  every RBAC change and user (de)activation or deletion increments in the same transaction.
- **System permissions**: `users:read/manage`, `settings:read/manage`, `roles:read/manage`,
  `permissions:read/manage`, `access:review/assign`. The `manage`/`assign` ones are reserved to the built-in
  Administrator role; administrators get no implicit data access. No CEO/Manager or other business role is built in.
- **Protections**: no self-assignment (except the installation superuser), administrator-only permissions cannot be
  delegated, system roles and permissions are immutable, the last administrator cannot be removed, roles in use
  cannot be deleted, every change is audited.
- **AI authorization context** (`GET /authorization/me/ai-context`, `AuthorizationEngine.ai_authorization_context`):
  the user's roles, permission keys, resources, tables, columns, row filters and flags, with no credentials, tokens,
  session data, email or system permissions; marked `is_authorization_token: false`.
- **CLI**: `python -m app.cli sync-permissions` restores the built-in permissions and Administrator role.

Frontend
- `PermissionsProvider` / `usePermissions()` (`hasPermission`, `canAccessResource`, `effectivePermissions`) backed by
  `GET /authorization/me`; navigation, route guards and buttons use it.
- Pages: Roles list, Role detail (permission editor with a review of changes), Permissions list, Permission
  create/edit with a form-based policy builder (tables, columns, operations, nested AND/OR row filters, deny rules,
  export/SQL flags), a server-validated review step with a plain-language summary and read-only JSON, and an access
  section on the User page (roles and direct grants/denials with expiry, effective access).

### Tables

`roles`, `permissions`, `role_permissions`, `user_roles`, `user_permissions`, `rbac_audit_events`,
`authorization_state`.

### Migrations

`0003_create_rbac_tables`: creates the tables above and seeds the ten system permissions, the Administrator role
holding them, and revision 1. Downgrade drops them.

### API endpoints (under `/api/v1`)

| Endpoint | Permission |
| --- | --- |
| `GET/POST /roles`, `GET/PATCH/DELETE /roles/{id}` | `roles:read` / `roles:manage` |
| `POST/PUT /roles/{id}/permissions`, `DELETE /roles/{id}/permissions/{permission_id}` | `roles:manage` |
| `GET/POST /permissions`, `GET/PATCH/DELETE /permissions/{id}` | `permissions:read` / `permissions:manage` |
| `POST /permissions/validate-policy` | `permissions:manage` |
| `GET /users/{id}/roles`, `/permissions`, `/effective-permissions` | `access:review` |
| `POST/DELETE /users/{id}/roles[/{role_id}]`, `POST/DELETE /users/{id}/permissions[/{permission_id}]` | `access:assign` |
| `GET /authorization/me`, `GET /authorization/me/ai-context` | signed in |
| `POST /authorization/check` | signed in (own access); `access:review` for another user |

### Frontend pages

`/admin/roles`, `/admin/roles/:roleId`, `/admin/permissions`, `/admin/permissions/new`,
`/admin/permissions/:permissionId`, and the access section on `/admin/users/:userId`.

### New configuration

`AUTHORIZATION_POLICY_MAX_DEPTH` (4), `AUTHORIZATION_POLICY_MAX_CONDITIONS` (50), `AUTHORIZATION_CACHE_SIZE` (1024).
No new dependencies.

### Commands executed and results (Module 04)

| Command | Result |
| --- | --- |
| `pytest` with `TEST_DATABASE_URL` (PostgreSQL 16) | **364 passed, 0 skipped** (55 s) |
| `pytest` without `TEST_DATABASE_URL` | **166 passed, 198 skipped** (skipped = the PostgreSQL tests) |
| `alembic upgrade head` on an empty database, `alembic check`, `alembic downgrade base`, `alembic upgrade head` | `0001 → 0002 → 0003`; `No new upgrade operations detected`; downgrade and re-upgrade clean |
| `python -m app.cli sync-permissions` after the migration | `0 change(s) made`; 10 system permissions, role `administrator`, revision 1 |
| `npx vitest run` | **93 passed** in 19 files |
| `npx tsc -b` | OK, no errors |
| `npm run build` | OK; JS 603 kB (178 kB gzip); Vite chunk-size warning as before |
| Headless Chromium against uvicorn + Vite on a fresh database | Signed in as the administrator; created a permission with an OR group through the builder; a contradictory filter was rejected with the server's message; created a role, added the permission after the review step; created a user, assigned the role; the user page showed the effective row filter |

Backend test coverage added:

| Area | File | Tests |
| --- | --- | --- |
| Policy validation: every operator and value type, groups, depth and size limits, contradictions, duplicates, normalisation, overrides | `tests/rbac/test_policy_validation.py` | 58 |
| Assignments and the engine: deny by default, explicit deny wins, expiry and revocation, inactive users/roles/permissions, row and column restrictions, multiple roles, self-assignment and escalation attempts, last administrator, 401/403 | `tests/rbac/test_assignments_and_authorization.py` | 31 |
| Roles and permissions API: CRUD, search, system protections, invalid policies, audit history | `tests/rbac/test_roles_and_permissions_api.py` | 13 |
| AI context content and exclusions, cache hits, bulk resolution, revision bumps, cache bounds | `tests/rbac/test_ai_context_and_cache.py` | 9 |
| Built-in catalog repair | `tests/rbac/test_system_catalog.py` | 3 |

Frontend tests added: policy builder model (6), roles list and creation (6), permission builder, review, server
errors and read-only views (7), role permission assignment (3), user role assignment and effective access (5),
`usePermissions` and navigation (4).

Not run in Module 04: a native Windows run, Docker Compose (the images are unchanged apart from application code),
load or penetration testing.

### Security limitations and known gaps

- **Data permissions are not enforced on data yet.** No query reaches business data until the SQL security pipeline
  exists; the engine returns structured decisions for that module to apply.
- **Table and column names are not checked against real schemas** until schema registration exists.
- **The UI decides only what to show.** Every request is authorized by the server.
- **`POST /authorization/check`** is a diagnostic endpoint; its answer is not a token and must not be trusted by clients.
- **The cache is per process**, but correctness does not depend on it (one revision lookup per decision).
- **Audit events** are readable per role and permission (history), but there is no global audit viewer or retention policy.
- **No approval workflow** for access changes, and no bulk assignment.
- The engine has been tested extensively but has had **no external security review**; this is not a
  production-readiness claim.

## Module 03 record

Backend
- **Passwords**: Argon2id (argon2-cffi, RFC 9106 low-memory profile: t=3, m=64 MiB, p=4), NFKC-normalised, never
  truncated (12 to 256 characters). Policy rejects common passwords, passwords made only of spaces, and passwords that
  are essentially the email, username or name. Hashing runs off the event loop; a dummy hash equalises timing for
  unknown accounts.
- **Sessions**: each sign-in creates a `user_sessions` row. Access tokens are HS256 JWTs (15 minutes; `iss`, `aud`,
  `typ`, `sub`, `sid`, `jti`) and every request re-checks the session and user in the database, so revocation and
  deactivation take effect immediately. Refresh tokens are 256-bit opaque values stored only as SHA-256 digests,
  rotated on every use under a row lock. Reuse of a rotated token revokes the session (theft detection), except within
  a 20-second grace window, which answers `409` so two tabs refreshing at once do not sign the user out. Idle expiry 7
  days, absolute expiry 30 days.
- **Transport and CSRF**: access token in the JSON body (kept in browser memory only); refresh token only in the
  `opspilot_refresh` cookie (`HttpOnly`, `SameSite=Strict`, `Path=/api/v1/auth`, `Secure` by default outside
  development/test; production refuses `AUTH_COOKIE_SECURE=false`). `/refresh` and `/logout` also require the
  `X-OpsPilot-CSRF: 1` header and an allowed `Origin`/`Referer`. Token responses are `Cache-Control: no-store`.
- **Brute-force protection**: progressive lockout after 5 consecutive failures (60 s, doubling, capped at 900 s, always
  expires); in-memory sliding-window rate limits per IP and per account on login, forgot/reset password and password
  change, per IP on refresh (`429` with `Retry-After`). Login failures are one generic message whether the account is
  missing, inactive, locked or the password is wrong.
- **Password reset**: single-use tokens (hash stored, 30 minutes, earlier ones invalidated). There is **no email
  delivery**: `forgot-password` always returns the same `202` message and hands the token to a pluggable notifier whose
  default sends nothing. Administrators can create a one-time reset link from the Users page (shown once, token in the
  URL fragment); this can be turned off with `PASSWORD_RESET_ADMIN_LINKS_ENABLED=false`. A reset or password change
  ends every session.
- **Authorization**: one dependency, `require_permission(Permission.X)`, backed by a replaceable
  `AuthorizationPolicy` (`get_authorization_policy` is the Module 04 extension point). Bootstrap policy: the
  installation administrator (`is_superuser`) has every permission; other users can sign in and manage their own
  account only. Permissions: `users:read`, `users:manage`, `settings:read`, `settings:manage`.
- **Account rules**: privileges are never assignable through the API (`is_superuser` is rejected by the schemas);
  the installation administrator and your own account cannot be deactivated or deleted; deactivation and deletion
  revoke all sessions and unused reset tokens; deletion is a soft delete, and the email/username become reusable.
- **Audit events**: `auth_events` rows written in the same transaction as the change (sign-in success/failure,
  refresh-token reuse, sign-out, sign-out everywhere, password change, reset requested/completed, account created/updated/activated/deactivated/deleted,
  sessions revoked, admin bootstrap). They never contain passwords or tokens. There is no API to read them yet.
- **Admin bootstrap**: `python -m app.cli create-admin` (interactive, hidden prompts, policy re-prompt) or
  `--no-input` with `OPSPILOT_ADMIN_EMAIL`, `OPSPILOT_ADMIN_FULL_NAME`, `OPSPILOT_ADMIN_PASSWORD`. Refuses once an
  administrator exists (PostgreSQL advisory lock against races); no default credentials; credentials are never
  logged or echoed. `python -m app.cli reset-password` is the documented recovery path. No HTTP endpoint can create
  an administrator.

Frontend
- `AuthSession` keeps the access token in memory only, restores the session on load through the refresh cookie,
  de-duplicates concurrent refreshes (one rotation), retries once after a `409`, and syncs sign-out across tabs
  (`BroadcastChannel`). The API client retries a request once after a `401`; there are no refresh loops.
- `AuthProvider`, route guards (`RequireAuth`, `RequirePermission`, `RedirectIfAuthenticated`), return to the
  requested page after sign-in, TanStack Query cache cleared on sign-out.
- Pages: sign in, forgot password, reset password, account (profile, change password, sign out everywhere), users
  list (search, status filter, sort, pagination, all kept in the URL), user detail (edit, activate, deactivate,
  revoke sessions, reset link, delete, active sessions), create-user dialog, 403 page.
- Forms use React Hook Form and Zod; destructive actions use a confirmation dialog.

### Tables

| Table | Purpose | Notable columns and constraints |
| --- | --- | --- |
| `users` | Accounts | `email`, `username` (nullable), `full_name`, `password_hash`, `is_active`, `is_superuser`, `is_verified`, `failed_login_attempts`, `locked_until`, `last_login_at`, `password_changed_at`, timestamps, `deleted_at`; partial unique indexes on live `email` and `lower(username)` |
| `user_sessions` | One row per signed-in browser | `user_id` FK, `refresh_token_hash` (unique), `expires_at`, `absolute_expires_at`, `last_used_at`, `rotated_at`, `revoked_at`, `revoked_reason`, optional `ip_address` / `user_agent` |
| `retired_refresh_tokens` | Rotated refresh token digests, for reuse detection | `session_id` FK (cascade), `token_hash` (unique), `retired_at` |
| `password_reset_tokens` | Single-use reset tokens | `user_id` FK, `token_hash` (unique), `expires_at`, `used_at`, `invalidated_at` |
| `auth_events` | Audit trail | `event_type`, `user_id`, `actor_user_id`, `ip_address`, `details jsonb`, `created_at` |

### Migrations

| Revision | File | Change |
| --- | --- | --- |
| `0001` | `backend/migrations/versions/2026_11_25_0001_create_application_settings.py` | Module 02, unchanged |
| `0002` | `backend/migrations/versions/2026_11_25_0002_create_authentication_tables.py` | Create the five tables above (downgrade drops them) |

### API endpoints

| Method and path | Auth | Result |
| --- | --- | --- |
| `POST /api/v1/auth/login` | none | `200` token + cookie, `401` generic, `429` |
| `POST /api/v1/auth/refresh` | cookie + CSRF | `200` rotated, `401`, `403` CSRF, `409` concurrent refresh, `429` |
| `POST /api/v1/auth/logout` | cookie + CSRF | `204`, cookie cleared |
| `POST /api/v1/auth/logout-all` | bearer | `204`, every session of the user ended |
| `GET /api/v1/auth/me` | bearer | `200` current user and permissions |
| `POST /api/v1/auth/change-password` | bearer | `200` new session (all others ended), `422` |
| `POST /api/v1/auth/forgot-password` | none | always `202` with the same message, `429` |
| `POST /api/v1/auth/reset-password` | none | `200`, `400` invalid/used/expired token, `422` policy |
| `GET /api/v1/users` | `users:read` | page; `q`, `status` (active/inactive/locked), `sort`, `order`, `offset`, `limit` |
| `POST /api/v1/users` | `users:manage` | `201`, `409` duplicate, `422` |
| `GET /api/v1/users/{id}` | `users:read` | `200` with active session count, `404` |
| `GET /api/v1/users/{id}/sessions` | `users:read` | active sessions |
| `PATCH /api/v1/users/{id}` | `users:manage` | email, username, name |
| `POST /api/v1/users/{id}/activate` / `deactivate` / `revoke-sessions` | `users:manage` | `409` for protected accounts |
| `POST /api/v1/users/{id}/password-reset` | `users:manage` | one-time link (`404` when admin links are disabled) |
| `DELETE /api/v1/users/{id}` | `users:manage` | `204` soft delete |
| `/api/v1/settings…` (Module 02) | `settings:read` / `settings:manage` | now `401` / `403` without them |

### New configuration

`ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`, `SESSION_ABSOLUTE_MAX_DAYS`, `AUTH_COOKIE_SECURE`,
`AUTH_COOKIE_SAMESITE`, `AUTH_COOKIE_DOMAIN`, `AUTH_RECORD_CLIENT_METADATA`, `FRONTEND_BASE_URL`,
`PASSWORD_RESET_TOKEN_EXPIRE_MINUTES`, `PASSWORD_RESET_ADMIN_LINKS_ENABLED`, `LOGIN_MAX_FAILED_ATTEMPTS`,
`LOGIN_LOCKOUT_BASE_SECONDS`, `LOGIN_LOCKOUT_MAX_SECONDS`, `AUTH_RATE_LIMIT_*`, `AUTH_REFRESH_RATE_LIMIT_PER_IP`
(all documented in `.env.example` and passed through `docker-compose.yml`). CLI only: `OPSPILOT_ADMIN_EMAIL`,
`OPSPILOT_ADMIN_FULL_NAME`, `OPSPILOT_ADMIN_PASSWORD`. The `POSTGRES_*` Compose setup is unchanged.

### Dependencies

Backend: `argon2-cffi==25.1.0`, `PyJWT==2.15.1`, `email-validator==2.4.0`. Frontend: `react-hook-form@7.92.0`,
`zod@4.6.7`, `@hookform/resolvers@7.0.9`; dev: `@testing-library/user-event@14.6.13`.

### Commands executed and results (Module 03)

| Command | Result |
| --- | --- |
| `alembic revision --autogenerate` against the test database, tidied into `0002`; `alembic upgrade head`, `alembic check` | Tables and named constraints created; `No new upgrade operations detected` |
| `pytest` with `TEST_DATABASE_URL` (PostgreSQL 16) | **250 passed, 0 skipped** |
| `pytest` without `TEST_DATABASE_URL` | **106 passed, 144 skipped** (skipped = the PostgreSQL tests) |
| `npx vitest run` | **62 passed** in 13 files |
| `npx tsc -b` | OK, no errors |
| `npm run build` | OK; JS 534 kB (164 kB gzip); Vite warns that the chunk is over 500 kB |
| `docker compose up -d` (images built with the sandbox-only CA line, see Known issues) | db, backend, frontend **healthy**; log shows `Running upgrade 0001 -> 0002` |
| `docker compose exec backend python -m app.cli create-admin` | Hidden prompts, weak password re-prompted, administrator created |
| Same with `--no-input` and `OPSPILOT_ADMIN_*` (fresh volume); then a second run | Created; second run refused with "An installation administrator already exists" |
| `docker compose logs backend` searched for the passphrase | Not present |
| `curl` login through the Compose backend | `200`, `Set-Cookie: opspilot_refresh=…; HttpOnly; Path=/api/v1/auth; SameSite=strict`, CORS credentials headers |
| Headless Chromium on the Compose frontend (`:5173`) and on Vite + uvicorn | Redirect to sign-in and back to the requested page; create user; reload keeps the session; `localStorage` empty; refresh cookie not readable by script; deactivated user gets the generic failure; sign-out survives a reload |

Test coverage by area:

| Area | File | Tests |
| --- | --- | --- |
| Auth API: login, generic failures, lockout and expiry, rate limits, refresh rotation, reuse detection, concurrent refresh, CSRF and Origin, cookie flags, logout, logout-all, me, change password, forgot/reset password, no tokens in logs | `tests/auth/test_auth_api.py` | 35 |
| Users API: permissions (401/403), create/duplicates/validation, list search/filter/sort/pagination, update, activate/deactivate, protected accounts, revoke sessions, reset links, delete and reuse of email | `tests/auth/test_users_api.py` | 42 |
| Admin bootstrap: env and prompt, refuses a second admin, weak password, nothing logged | `tests/auth/test_bootstrap.py` | 4 |
| Password policy and hashing | `tests/test_passwords.py` | 11 |
| Tokens, JWT validation, rate limiter, authorization policy, email normalisation | `tests/test_auth_primitives.py` | 21 |
| Module 02 database tests (updated for the new tables and migration `0002`) | `tests/database/*` | 73 |
| Settings API (now authenticated) and Module 01 tests | `tests/test_settings_api.py`, `test_config.py`, `test_database_health.py`, `test_errors.py`, `test_logging.py`, `test_system.py` | 64 |
| Frontend: auth session, API client, guards, sign-in, reset pages, account, users list, user detail, app and Module 01 components | 13 files under `frontend/src` | 62 |

Not run in Module 03: a native Windows run (only Linux was available), real email delivery (none exists), and load
or penetration testing.

### Security limitations and known gaps

- **Rate limits are in memory and per process.** They reset on restart and are not shared between replicas; a
  multi-instance deployment needs a shared store (the `RateLimiter` protocol is the place to plug one in).
- **Client IP is the direct peer address.** Behind a reverse proxy every client shares the proxy's IP for rate limits
  and session metadata; trusted `X-Forwarded-For` handling is not implemented.
- **Common-password list is small and local** (no breached-password / HIBP lookup).
- **No email delivery.** Self-service "forgot password" sends nothing; users must ask an administrator for a reset
  link, and the administrator sees that link once and passes it on out of band.
- **No email verification flow**; `is_verified` is stored but not enforced.
- **No MFA**, no per-user session list for the user themselves (administrators can see and revoke sessions).
- **Audit events have no read API or retention policy** yet.
- **Bootstrap permissions only**: administrator or ordinary user. (Status now: replaced by Module 04's roles and permissions.)
- **Every authenticated request reads the session and user rows** (immediate revocation at the cost of one query).
- **Without `SECRET_KEY`** (development only) a random key is generated at each start, so access tokens stop working
  after a restart and browsers silently refresh. Production requires `SECRET_KEY`.
- **`SameSite=Strict` cookies need the frontend and API on the same site** (e.g. `localhost:5173` and `localhost:8000`,
  or `app.example.com` and `api.example.com`).
- **Bundle size**: the frontend JS is 534 kB minified (Vite chunk-size warning); route-level code splitting is not done.
- This is not a production-readiness claim: there has been no external security review, and HTTPS termination,
  secret management and backups are deployment concerns not covered here.

## Module 02 record

### What was implemented

- **Async SQLAlchemy 2.x** throughout the application (`AsyncEngine`, `AsyncSession`, psycopg 3 async), typed
  declarative models (`Mapped`, `mapped_column`) only. Same `DATABASE_URL` format as Module 01.
- **Database infrastructure** (`app/database/`): shared `Base` with naming conventions and a type map
  (`datetime` → `timestamptz`, `uuid.UUID` → `uuid`, `dict` → `jsonb`) and `eager_defaults`; one engine per process
  with a configurable pool (size, overflow, timeout, recycle, pre-ping, connect timeout, `hide_parameters`, UTC
  sessions); request-scoped sessions (rollback on failure, always closed); `transaction()` for service-owned
  commit/rollback that joins when nested; reusable `check_database` health check.
- **Model foundation** (`app/models/base_model.py`): `UUIDPrimaryKeyMixin` (UUID v4, plus `gen_random_uuid()`
  default), `TimestampMixin` (`created_at` / `updated_at`, timezone-aware UTC, auto-updated), opt-in
  `SoftDeleteMixin` (`deleted_at`, `mark_deleted`, `restore`).
- **Repository layer**: `BaseRepository[Model]` (get, list, paginate, count, exists, add, create, update, delete,
  hard_delete; bounded lists; never commits; soft-delete aware) and `ApplicationSettingRepository`.
- **Application settings**: model, Pydantic schemas, repository, service and API. Secret-like keys and JSON members
  are rejected and, as a second line of defence, redacted in responses.
- **Error handling**: database connectivity failures on any endpoint return `503 DATABASE_UNAVAILABLE` with a generic
  message; other database errors return the generic `500`. Logs record only the exception type and SQLSTATE.
  New `ConflictError` (409) and `InvalidInputError` (422).
- **Alembic**: models registered via `import app.models`, URL from settings or `config.attributes`, type and server
  default comparison, sequential revision ids, tidied script template, first migration `0001`.
- **Test database**: `docker-compose.test.yml` (separate project, own credentials, in-memory, loopback only),
  `TEST_DATABASE_URL` read from the environment or `.env`, with a guard that refuses non-`test` databases and the
  `DATABASE_URL` database.
- **Integration**: `/health/database` now uses the shared health check through a real session; Docker Compose passes
  the new pool settings; Module 01 endpoints, startup, frontend and Compose setup are unchanged.

#### Models and tables

| Model | Table | Columns |
| --- | --- | --- |
| `ApplicationSetting` | `application_settings` | `id uuid PK`, `key varchar(128) NOT NULL`, `value jsonb NOT NULL`, `description text NULL`, `created_at timestamptz NOT NULL`, `updated_at timestamptz NOT NULL` |

Constraints: `pk_application_settings`, `uq_application_settings_key` (also the lookup/ordering index),
`ck_application_settings_key_format`, `ck_application_settings_value_not_null`, `ck_application_settings_value_size`,
`ck_application_settings_description_length`. No foreign keys yet.

#### Migrations

| Revision | File | Change |
| --- | --- | --- |
| `0001` | `backend/migrations/versions/2026_11_25_0001_create_application_settings.py` | Create `application_settings` (downgrade drops it) |

#### API endpoints

| Method and path | Result |
| --- | --- |
| `GET /api/v1/settings?offset&limit` | `200` page `{items, total, offset, limit}` ordered by key (`limit` 1–100, default 50) |
| `GET /api/v1/settings/{key}` | `200` setting, `404 NOT_FOUND` |
| `PUT /api/v1/settings/{key}` | `201` created / `200` replaced, `409 CONFLICT` on a concurrent create, `422 VALIDATION_ERROR` |
| `DELETE /api/v1/settings/{key}` | `204`, `404 NOT_FOUND` |
| All of the above | `503 SERVICE_UNAVAILABLE` (not configured) or `503 DATABASE_UNAVAILABLE` (unreachable) |

#### New configuration

`DATABASE_POOL_TIMEOUT_SECONDS` (10), `DATABASE_POOL_RECYCLE_SECONDS` (1800), now also passed through Compose with
`DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_CONNECT_TIMEOUT_SECONDS`. Tests only: `TEST_DATABASE_URL`,
and optionally `TEST_POSTGRES_USER` / `TEST_POSTGRES_PASSWORD` / `TEST_POSTGRES_PORT` for `docker-compose.test.yml`.
The existing `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` setup is unchanged.

#### Dependencies

`SQLAlchemy[asyncio]==2.1.9` (adds greenlet 3.5.6, required for async), dev: `pytest-asyncio==1.5.0`.

#### Files created

```
docker-compose.test.yml
backend/app/database/naming.py  transaction.py  health.py
backend/app/models/__init__.py  base_model.py  application_setting.py
backend/app/repositories/__init__.py  base_repository.py  application_setting_repository.py
backend/app/schemas/application_setting.py
backend/app/services/application_settings_service.py
backend/app/api/routes/settings.py
backend/migrations/versions/2026_11_25_0001_create_application_settings.py
backend/tests/database/__init__.py  conftest.py  test_conventions.py  test_models.py  test_repository.py
backend/tests/database/test_sessions_and_transactions.py  test_application_settings_service.py  test_migrations.py
backend/tests/test_settings_api.py
```

#### Files modified

```
.env.example  docker-compose.yml  README.md  ARCHITECTURE.md  PROJECT_STATUS.md
backend/requirements.txt  requirements-dev.txt  pytest.ini
backend/app/main.py  api/deps.py  api/router.py
backend/app/core/config.py  core/exceptions.py  core/logging.py (public is_sensitive_key)
backend/app/database/__init__.py  base.py  session.py
backend/app/services/health_service.py
backend/migrations/env.py  script.py.mako
backend/tests/conftest.py  test_config.py  test_database_health.py
```

Removed: `backend/tests/test_migrations.py` (replaced by `tests/database/test_migrations.py`),
`backend/migrations/versions/.gitkeep`.

#### Commands executed and results (Module 02)

| Command | Result |
| --- | --- |
| `pip install -r requirements-dev.txt` (Python 3.12 venv) | OK |
| `alembic revision --autogenerate` against the test database | Detected `application_settings`; file tidied into revision `0001` |
| `alembic upgrade head`, `\d application_settings`, `alembic check`, `alembic downgrade base` | Table and all named constraints present; `No new upgrade operations detected`; downgrade OK |
| `pytest` without `TEST_DATABASE_URL` | **73 passed, 63 skipped** (skipped = the PostgreSQL tests) |
| `docker compose -f docker-compose.test.yml up -d --wait`, then `pytest` with `TEST_DATABASE_URL` | **136 passed, 0 skipped** |
| `pytest` with `TEST_DATABASE_URL` naming a non-test database, or equal to `DATABASE_URL` | Run refused before any test, as intended |
| Backend image built from the committed Dockerfile (plus the sandbox-only CA line, see Known issues), `docker compose up db backend` with a `.env` holding only `POSTGRES_PASSWORD` | db and backend **healthy**; container log shows `Running upgrade -> 0001` |
| `curl` `/`, `/health`, `/health/database` | 200 / 200 / 200 `ok` |
| `curl` PUT (new) / PUT (existing) / GET list / PUT `smtp.password` / DELETE / GET deleted | 201 / 200 (same id, newer `updated_at`) / 200 / 422 / 204 / 404 |
| `psql \dt`, `select * from alembic_version` in the db container | `application_settings`, `alembic_version` = `0001` |
| `docker compose stop db`, then `/api/v1/settings`, `/health/database`, `/health` | 503 `DATABASE_UNAVAILABLE` (no URL/host/user in body; log has only `OperationalError`), 503 `unavailable`, 200; 200 again after `start db` |
| `docker compose restart backend` | Migrations re-run as a no-op, app starts |
| `uvicorn app.main:app --loop asyncio:SelectorEventLoop` (Windows workaround, checked on Linux) | Starts and serves database requests |

Test coverage by area (136 PostgreSQL-enabled tests):

| Area | File | Tests |
| --- | --- | --- |
| Conventions (no DB) | `tests/database/test_conventions.py` | 7 |
| Base model: UUID, timestamps, timezone, soft delete | `tests/database/test_models.py` | 8 |
| Repository: CRUD, missing, pagination, count/exists, constraints, rollback | `tests/database/test_repository.py` | 21 |
| Sessions and transactions: lifecycle, cleanup, rollback, nesting, independent sessions, pool return | `tests/database/test_sessions_and_transactions.py` | 10 |
| Settings service and schemas: CRUD, duplicates (409), invalid input, secrets rejected/redacted | `tests/database/test_application_settings_service.py` | 22 |
| Migrations: config, naming, empty → head → base → head, structure, models match migrations | `tests/database/test_migrations.py` | 5 |
| Settings API: list, create, update, delete, missing, invalid payloads, pagination, secrets, per-request sessions, database not configured / unavailable | `tests/test_settings_api.py` | 24 |
| Health (incl. new health check) and Module 01 tests | `tests/test_database_health.py`, `test_config.py`, `test_errors.py`, `test_logging.py`, `test_system.py` | 39 |

Not run in Module 02: the frontend test suite and frontend build (no frontend file changed), and a native Windows
run (only Linux was available; the Windows event-loop handling in `tests/conftest.py` and the documented
`--loop asyncio:SelectorEventLoop` option are untested on Windows).

### Limitations recorded at the end of Module 02 (status now)

- `DELETE` has no "protected setting" rule yet: any existing setting can be deleted. Rules (and who may change
  settings) arrive with authentication and permissions.
- ~~No authentication on the settings API yet.~~ Resolved in Module 03: the settings API requires `settings:read` /
  `settings:manage`.
- `updated_at` is maintained by SQLAlchemy, not a database trigger: raw SQL updates must set it.
- If migrations have not been applied, settings endpoints return a generic 500 (logged with SQLSTATE `42P01`); the
  container always applies them on start.
- Running the backend natively on Windows without `--reload` needs `--loop asyncio:SelectorEventLoop` (psycopg async
  limitation). Docker is unaffected.

## Module 01 record

### Completed features

Backend
- FastAPI app "OpsPilot AI", v1.0.0, with description, OpenAPI docs (`/docs`, `/redoc`, `/openapi.json`).
- Endpoints: `GET /`, `GET /health`, `GET /health/database` (no URLs, hosts or credentials in responses).
- Pydantic Settings for all required variables; NVIDIA settings optional; validation of environment, log
  level, port, CORS origins (no `*`), PostgreSQL-only URL, secret length, https AI endpoint; production requires
  `SECRET_KEY`, `DATABASE_URL` and `APP_DEBUG=false`. Validation errors never echo input values.
- SQLAlchemy 2.x engine (pre-ping, bounded pool, connect timeout), session factory, declarative `Base` with
  naming conventions, request-scoped session dependency (commit / rollback / close), engine disposal on
  shutdown.
- Alembic wired to settings (no URL in `alembic.ini`); the container runs `alembic upgrade head` on start.
- Error envelope `{"success": false, "error": {code, message, details?, request_id}}` for 404, 405, 422,
  application errors and unhandled exceptions (generic 500, traceback in logs only).
- JSON logs with timestamp, level, module, message, request ID; secret-looking fields redacted; query strings
  not logged. `X-Request-ID` on every response (safe incoming IDs are reused).
- CORS restricted to configured origins.

Frontend
- React 19 + TypeScript (strict) + Vite + Tailwind 4 + React Router + TanStack Query + Lucide icons.
- App shell, home page (product name, description, application / backend / database status, auto refresh),
  reusable 404 page, error boundary.
- Central API client: base URL and timeout from env, JSON handling, typed responses, `ApiError` for HTTP,
  network, timeout and malformed-JSON failures, headers provider reserved for authentication.

Infrastructure
- Backend Dockerfile (python:3.12-slim, non-root, health check), frontend Dockerfile (Node build → nginx,
  port 8080, SPA fallback, health check).
- `docker-compose.yml`: db, backend, frontend; persistent `postgres_data` volume; health checks;
  `depends_on: service_healthy`; `restart: unless-stopped`; database on an internal-only network and not
  published; required `POSTGRES_PASSWORD`.
- `docker-compose.dev.yml`: publishes PostgreSQL on 127.0.0.1 only, for local development.
- Root `.env.example` documenting every setting; `.env` git-ignored.

Documentation
- `README.md`, `ARCHITECTURE.md`, this file.

### Commands executed and results (Module 01)

| Command | Result |
| --- | --- |
| `python3.12 -m venv .venv && pip install -r requirements-dev.txt` | OK |
| `pytest` (no `TEST_DATABASE_URL`) | **37 passed, 4 skipped** (the 4 need PostgreSQL) |
| `TEST_DATABASE_URL=postgresql://…@127.0.0.1:55432/opspilot_test pytest` (postgres:16-alpine container) | **41 passed, 0 skipped** |
| `npm install --save-exact lucide-react@1.70.0`, `npm uninstall @testing-library/user-event` | OK, 0 vulnerabilities |
| `npx tsc -b` | OK, no errors |
| `npm test` (`vitest run`) | **24 passed** in 6 files |
| `npm run build` | OK (JS 367 kB, 115 kB gzip) |
| `docker compose config -q` without `.env` | Fails as intended: `required variable POSTGRES_PASSWORD is missing a value` |
| `docker compose config -q` with `.env` (base and with dev override) | OK |
| `docker compose build` | Backend `pip install` failed in this sandbox: TLS-intercepting proxy CA not trusted inside build containers (environment-specific, see Known issues) |
| Images built from the same Dockerfiles plus a local-only CA line, then `docker compose up -d --no-build` | db, backend, frontend all **healthy** |
| `curl localhost:8000/` / `/health` / `/health/database` / `/nope` | 200 / 200 / 200 `ok` (17 ms) / 404 envelope |
| CORS preflight-style check with `Origin: http://localhost:5173` | `access-control-allow-origin: http://localhost:5173` |
| `curl localhost:5173/` and `/some/deep/route` | 200 / 200 (SPA fallback) |
| `psql \dt` in the db container | `alembic_version` exists (migrations ran) |
| `docker compose stop db`, then `/health/database` | **503 `unavailable`**, `/health` still 200; back to 200 after `start db` |
| Insert row, `docker compose down`, `up db`, select row | Row survived (volume persists) |
| `docker compose ps` | db port not published (`5432/tcp` only); with dev override `127.0.0.1:5432` |
| Backend container `id`, `env | grep -c PASSWORD` | uid 10001 (non-root); 0 password variables in backend |
| Headless Chromium on `http://localhost:5173` | Backend API and Database cards show **Connected**; 404 page renders; no console errors; checked at 1280 px and 390 px |
| Local route: `alembic upgrade head`, `uvicorn app.main:app`, `npx vite` with root `.env` | Backend reads root `.env`; Vite dev server serves the app; browser shows both cards Connected |

## Known issues

- **Docker build behind a TLS-intercepting proxy:** in the verification sandbox, `pip install` / `npm ci` inside
  `docker compose build` could not verify certificates because the sandbox's egress proxy CA is not in the
  base images. The images were therefore built from copies of the committed Dockerfiles with one extra line
  trusting that CA (`PIP_CERT` / `NODE_EXTRA_CA_CERTS`); that line is not committed. On a normal network
  `docker compose up --build` needs no change. Behind a corporate proxy, add its CA to the build.
- Recharts is listed in the planned stack but intentionally not installed yet (no module draws charts yet).
- PostgreSQL keeps the password from its first start; changing `POSTGRES_PASSWORD` later requires changing it
  in the database or recreating the volume.
- `VITE_API_BASE_URL` is fixed at build time; changing it requires rebuilding the frontend image.

## Next module

Module 06, as defined by the project owner. Not started: waiting for review and approval of Module 05.
