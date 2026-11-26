# OpsPilot AI architecture

This document describes how OpsPilot AI is deployed and how its parts fit together. Sections marked
**planned** describe later modules; Modules 01 (foundation), 02 (application database layer) and 03 (authentication
and user management) are implemented.

## 1. Independent installation model

Every company runs its own, separate installation of OpsPilot AI on infrastructure it controls:

```
Company A                                  Company B
┌───────────────────────────────┐          ┌───────────────────────────────┐
│ OpsPilot frontend + backend   │          │ OpsPilot frontend + backend   │
│ OpsPilot application database │          │ OpsPilot application database │
│ ── calls ──> A's GET SQL API  │          │ ── calls ──> B's GET SQL API  │
└───────────────────────────────┘          └───────────────────────────────┘
```

- One installation serves exactly one company. Its users, roles, permissions, registered schemas, API
  configuration and conversations belong to that installation only.
- There is no shared control plane, no tenant identifier and no cross-installation data. Isolation comes from
  separate deployments, not from row-level tenant filtering.
- All behaviour is configured per installation through environment variables (`.env`), so the same images are
  reused by every company.

## 2. Frontend / backend separation

| Layer | Responsibility | Never does |
| --- | --- | --- |
| Frontend (React SPA) | Presentation, routing, client-side state, calling the backend through one API client | Hold secrets, build SQL, call the company API or the LLM directly |
| Backend (FastAPI) | Configuration, validation, orchestration, security decisions, persistence, calls to external systems | Render UI |

The two communicate only over HTTP/JSON. The frontend's base URL comes from `VITE_API_BASE_URL`; the backend
allows browser calls only from origins in `CORS_ORIGINS`. Every failed response uses one error envelope
(`{"success": false, "error": {...}}`) and carries an `X-Request-ID` that also appears in the backend logs.

The API client takes its bearer token and its refresh-on-401 behaviour from the authentication layer
(`ApiClient.useAuthentication`), so feature code never handles tokens (section 4.7).

## 3. Application database (Module 02)

OpsPilot's PostgreSQL database stores **OpsPilot's own data**, never the company's business data. The company's
database is reached only through its GET SQL API (section 6); OpsPilot never opens a connection to it.

- Implemented: `application_settings` (non-secret installation settings); `users`, `user_sessions`,
  `retired_refresh_tokens`, `password_reset_tokens`, `auth_events` (Module 03, section 4).
- **Planned:** roles, permissions, registered schema metadata, external API configuration,
  conversations and answers, and audit records of every generated and executed query.

### 3.1 Layers

```
FastAPI route        app/api/routes/*.py       validate input, call one service, return a schema
     │
Service              app/services/*.py         business rules, transaction boundaries
     │
Repository           app/repositories/*.py     queries and persistence (flush, never commit)
     │
AsyncSession         app/database/session.py   one per request, from the process-wide engine
     │
Models               app/models/*.py           tables, constraints, indexes
     │
PostgreSQL
```

Route handlers never receive a session or build queries; they depend on a service from `app/api/deps.py`. There is
no endpoint that executes arbitrary SQL, and the frontend never sees database credentials.

### 3.2 Async SQLAlchemy 2.x

The application uses `AsyncEngine` / `AsyncSession` with the psycopg 3 driver (`postgresql+psycopg://`, the same URL
format as Module 01: SQLAlchemy selects psycopg's async mode for the async engine). Models use the typed declarative
API (`Mapped[...]`, `mapped_column`) only.

| File | Role |
| --- | --- |
| `database/base.py` | The single `Base` (shared `MetaData`, naming convention, type map, `eager_defaults`) |
| `database/naming.py` | Constraint and index naming convention |
| `database/session.py` | `create_engine_from_settings`, `Database` (engine + session factory, `ping`, `session()`, `dispose`) |
| `database/transaction.py` | `transaction(session)`: service-level commit/rollback with join-on-nest semantics |
| `database/health.py` | `check_database`: `SELECT 1` through a real session, with a timeout; returns only status, latency and the exception type |
| `models/base_model.py` | `UUIDPrimaryKeyMixin`, `TimestampMixin`, `SoftDeleteMixin` |
| `repositories/base_repository.py` | `BaseRepository[Model]`, `Page` |

### 3.3 Model conventions

- **One Base.** Every model inherits `app.database.base.Base` and is imported in `app/models/__init__.py` so
  Alembic sees it.
- **Tables** are plural `snake_case` (`application_settings`); **columns** are `snake_case`; foreign-key columns
  are `<referenced_singular>_id` (`role_id`).
- **Primary keys** are UUID v4 (`UUIDPrimaryKeyMixin`): generated in Python so the id is known before flush, with a
  `gen_random_uuid()` server default as well. Sequential integers are not used for exposed identifiers.
- **Timestamps** (`TimestampMixin`): `created_at` and `updated_at` are `timestamptz NOT NULL DEFAULT now()`;
  `updated_at` is refreshed by every ORM/Core `UPDATE`. Connections run with `timezone=UTC`, so values come back as
  timezone-aware UTC datetimes. `Mapped[datetime]` always maps to `DateTime(timezone=True)`; naive timestamps cannot
  be declared by accident. Raw SQL updates must set `updated_at` themselves.
- **Soft delete** (`SoftDeleteMixin`, nullable `deleted_at`) is opt-in per model, used only where hiding a row is the
  business meaning of delete. Audit and history tables never use it and are never cascade-deleted.
  `application_settings` does not use it (a deleted setting is simply gone).
- **Nullability** is explicit through the annotation: `Mapped[str]` is `NOT NULL`, `Mapped[str | None]` is nullable.
- **Strings** have a length (`String(128)`) unless they are free text (`Text`).
- **JSON** uses `JSONB`, never `JSON`, with `none_as_null=True` so Python `None` is SQL `NULL`. Replace JSON values;
  do not mutate them in place (in-place changes are not tracked).
- **Defaults** that matter for data integrity are database defaults (`server_default`), so rows written outside the
  ORM are correct too.
- **Constraints over code:** uniqueness, formats, sizes and non-null rules are database constraints, with Pydantic
  validation in front of them for friendly errors.
- **Foreign keys** (from later modules) must state `ondelete` explicitly; `CASCADE` only for rows that are pure
  children of their parent, never for audit or history rows (use `RESTRICT` or `SET NULL`).
- **Relationships** (from later modules) must be loaded explicitly (`selectinload` / `joinedload`) or declared
  `lazy="raise"`: implicit lazy loads are not possible in async code and hide N+1 queries.
- **Indexes** are added only for a concrete query, with a comment saying which one. Unique constraints already
  create an index.

Naming convention (applied through `Base.metadata`, so migrations are predictable):

| Object | Pattern | Example |
| --- | --- | --- |
| Primary key | `pk_<table>` | `pk_application_settings` |
| Foreign key | `fk_<table>_<column>_<referred table>` | `fk_users_role_id_roles` |
| Unique | `uq_<table>_<columns>` | `uq_application_settings_key` |
| Index | `ix_<table>_<columns>` | `ix_users_created_at` |
| Check | `ck_<table>_<name>` | `ck_application_settings_key_format` |

### 3.4 Session lifecycle

- **One engine per process**, created in the FastAPI lifespan (`Database.from_settings`) and disposed at shutdown.
  Engine creation is lazy, so the app starts while PostgreSQL is still booting.
- **Pool** (all configurable, see README): `pool_size` 5, `max_overflow` 10, `pool_timeout` 10 s,
  `pool_recycle` 1800 s, `pool_pre_ping` on, `connect_timeout` 5 s. `hide_parameters=True` keeps bound values out
  of error messages and logs.
- **One session per request.** `get_db_session` opens a new `AsyncSession` for each request that needs one; sessions
  are never global or shared. It rolls back if the request fails and always closes the session, returning the
  connection to the pool. Closing discards anything that was not committed.
- `expire_on_commit=False` and `eager_defaults` mean objects stay readable after commit without hidden queries.

### 3.5 Transaction management

- **Repositories never commit.** They `flush`, so constraint errors surface at the call site.
- **Services own the boundary.** A write operation runs inside `async with transaction(session):`, which commits
  once on success and rolls back everything on any exception.
- **Nesting joins.** If a service inside a `transaction` block calls another service that opens one, the inner
  block only flushes; the outermost block commits or rolls back. A nested call cannot commit half of its caller's
  work. To recover from a failed sub-step and continue, use a savepoint (`session.begin_nested()`).
- **Reads** need no transaction block; the implicit read transaction ends when the request's session closes.
- The service, not the repository, translates expected database errors: a unique violation from a concurrent insert
  becomes `409 CONFLICT`.

### 3.6 Repository pattern

`BaseRepository[Model]` gives each repository `get`, `list`, `paginate`, `count`, `exists`, `add`, `create`,
`update`, `delete` and `hard_delete`. It is deliberately small:

- Lists are always bounded (`limit` 1–100) and ordered (`default_order_by`, falling back to `id`).
- A missing row is `None` / `False`; raising `NotFoundError` is the service's choice.
- `update` refuses unknown columns and the managed fields (`id`, `created_at`, `updated_at`, `deleted_at`).
- Database exceptions are never swallowed.
- For `SoftDeleteMixin` models every read excludes deleted rows unless `include_deleted=True`, and `delete` sets
  `deleted_at`. **Specialised queries in subclasses must add `Model.deleted_at.is_(None)` themselves** (or start
  from `self._select()`).

Specialised queries are plain methods on the subclass (`ApplicationSettingRepository.get_by_key`).

### 3.7 Database constraints (`application_settings`)

| Constraint / index | Why |
| --- | --- |
| `pk_application_settings` (UUID) | Identity |
| `uq_application_settings_key` | One row per key; its index serves lookups by key and the list ordering, so no other index is needed |
| `ck_application_settings_key_format` (`^[a-z][a-z0-9_.-]*$`, `varchar(128)`) | Keys are stable identifiers, safe in URLs |
| `ck_application_settings_value_not_null` + `NOT NULL` | A setting always has a JSON value; JSON `null` is rejected too |
| `ck_application_settings_value_size` (32 KB of JSONB text) | Backstop for the API's 16 KB limit |
| `ck_application_settings_description_length` (1000) | Bounded free text |

Settings must never hold secrets: keys and JSON members that look like credentials (`password`, `secret`, `token`,
`api_key`, `credential`, ...) are rejected with 422, and the response schema redacts them if a row was written some
other way. Secrets get dedicated storage in the connector and security modules.

### 3.8 Migration strategy

- Alembic reads the URL from settings (`DATABASE_URL`), never from `alembic.ini`. Tests pass a URL through
  `config.attributes["database_url"]`.
- `env.py` imports `app.models`, so autogenerate compares against every model; type and server-default changes are
  compared too.
- Migrations run synchronously (psycopg 3 sync mode). They are a one-shot CLI step, so an async environment would add
  complexity for no benefit.
- Revision ids are sequential (`0001`, `0002`, ...) and files are named `YYYY_MM_DD_<rev>_<slug>.py`.
- Every migration has a working `downgrade`. A migration that has been applied anywhere is never edited; changes go
  in a new revision.
- The backend container runs `alembic upgrade head` before serving.
- A test asserts that autogenerate finds no difference between the models and the migrated schema, so a model change
  without a migration fails the suite.

### 3.9 Health and failure behaviour

`GET /health/database` uses `check_database` (a `SELECT 1` through a pooled session, with a timeout). Any request
that cannot reach the database returns `503 DATABASE_UNAVAILABLE` ("The application database is unavailable.");
the log records only the exception type and SQLSTATE. Other database errors return the generic 500. No response
contains a URL, host, user name, password or driver message.

### 3.10 Test database strategy

- Database tests run against a **separate, disposable** database from `TEST_DATABASE_URL` (environment or `.env`),
  for example the in-memory container in `docker-compose.test.yml` with its own credentials. They are marked
  `postgres` and skipped when it is unset.
- Safety guard: the run stops if that database's name does not contain `test` or if it is the same database as
  `DATABASE_URL`.
- At the start of a run the test database is downgraded to base and upgraded to head; each test then empties the
  tables it may have written.
- The migration cycle test (empty → head → base → head) runs in a brand-new database created for the test and dropped
  afterwards.
- A test-only model with every mixin (including soft delete) lives on its own metadata, so it never enters the
  application schema.

## 4. Authentication and user management (Module 03)

### 4.1 Overview

```
Browser (React)                                   Backend (FastAPI)                         PostgreSQL
 access token: JS memory only ── Authorization: Bearer ──> get_current_principal ──> user_sessions ⋈ users
 refresh token: HttpOnly cookie ── /api/v1/auth/refresh ──> AuthService.refresh ───> rotate digest, retire old
```

| Piece | Where |
| --- | --- |
| Settings (lifetimes, cookie, lockout, rate limits) | `app/core/config.py` |
| Token primitives (JWT, opaque tokens, digests) | `app/core/security.py` |
| Password hashing and policy | `app/services/password_service.py` |
| Login, refresh, logout, password change and reset | `app/services/auth_service.py` |
| User administration rules | `app/services/user_service.py` |
| Authorization (permissions, policy) | `app/core/authorization.py`, `require_permission` in `app/api/deps.py` |
| Rate limiting | `app/core/rate_limit.py` |
| Audit events | `app/services/audit_service.py`, `auth_events` table |
| First administrator | `app/services/bootstrap_service.py`, `python -m app.cli` |

There is one user database per installation: no tenant column, no organisation table.

### 4.2 Data model

| Table | Purpose | Key points |
| --- | --- | --- |
| `users` | Accounts | UUID PK; `email` stored normalised (trimmed, IDNA domain, lower case; `CHECK email = lower(email)`) and unique among non-deleted rows (partial unique index `ix_users_email_live`); optional `username`, unique case-insensitively among non-deleted rows; `password_hash` (Argon2id, never returned); `is_active`, `is_verified`, `is_superuser`; `failed_login_attempts`, `locked_until`; `last_login_at`, `password_changed_at`; `created_at`/`updated_at`/`deleted_at` (soft delete). |
| `user_sessions` | One row per signed-in browser (a refresh-token *family*) | `refresh_token_hash` (SHA-256 hex of the current token, unique); `expires_at` (idle expiry, moves on rotation) and `absolute_expires_at`; `revoked_at` + `revoked_reason`; `last_used_at`, `rotated_at`; optional `user_agent`, `ip_address`. FK to `users` `ON DELETE CASCADE`; indexes on `user_id`, `expires_at`. |
| `retired_refresh_tokens` | Digests of tokens rotated away | PK `token_hash`, FK `session_id` (cascade). Only used to recognise reuse. |
| `password_reset_tokens` | Single-use reset tokens | `token_hash` (unique), `expires_at`, `used_at`, `invalidated_at` (a newer token or a password change supersedes it). |
| `auth_events` | Append-only security log | `event_type`, `user_id`, `actor_user_id` (both `SET NULL` on delete), `ip_address`, `details` (JSONB of simple values). |

Migration `0002` creates them; `downgrade` drops them. Raw tokens and passwords are never stored.

### 4.3 Token strategy

- **Access token:** HS256 JWT signed with `SECRET_KEY`, 15 minutes by default. Claims: `sub` (user id), `sid`
  (session id), `iss`, `aud`, `typ=access`, `iat`, `exp`, `jti`. No email, name or privilege claims: nothing in the
  token is trusted beyond identity. Verification pins the algorithm (no `none`, no algorithm confusion), audience,
  issuer and type.
- **Revalidation on every request:** `get_current_principal` loads the session and user in one query and requires
  the session to be unrevoked and unexpired and the user to be active and not deleted. Logout, revocation,
  deactivation, deletion and password changes therefore take effect on the **next request**, not when the access
  token expires. The cost is one indexed primary-key join per authenticated request, acceptable for a
  single-company installation.
- **Refresh token:** 256 random bits (`secrets.token_urlsafe(32)`), stored as SHA-256 (no salt needed: the token has
  full entropy, so there is nothing to brute-force). Sent only as the `opspilot_refresh` cookie: `HttpOnly`,
  `SameSite=Strict`, `Path=/api/v1/auth`, `Secure` outside development/test, `Max-Age` = session expiry.
- **Rotation:** every `/refresh` replaces the session's digest with a new token's digest and moves the old digest to
  `retired_refresh_tokens`, under a row lock (`SELECT … FOR UPDATE`) so concurrent refreshes serialise.
- **Reuse detection:** a presented token that matches a *retired* digest means a copy of an old token is in use.
  The whole session is revoked (`revoked_reason=token_reuse`) and a `refresh_token_reuse` event is recorded, so
  both the attacker's and the victim's copies stop working. Exception: within `REFRESH_REUSE_GRACE_SECONDS` (20 s)
  of the rotation the answer is `409 REFRESH_CONFLICT` without revocation, because two tabs of the same browser
  refreshing at the same moment look exactly like reuse; the losing tab retries with the cookie the winner
  received. A replay inside that window gains nothing (no tokens are returned).
- **Lifetimes:** idle expiry 7 days (moves forward on each rotation), absolute expiry 30 days.
- **Cleanup:** on each login, the user's expired sessions and those revoked longer than the refresh lifetime are
  deleted (with their retired digests), so network metadata is not kept longer than useful.

Trade-offs considered: server-side sessions only (opaque cookie on every request) would avoid JWTs but need a CSRF
defence on every endpoint; pure stateless JWTs would make revocation and deactivation wait for expiry. The hybrid
keeps API requests CSRF-free (bearer header) and revocation immediate (per-request session check).

### 4.4 CSRF

Only `/auth/refresh` and `/auth/logout` are authenticated by the cookie. They require the custom header
`X-OpsPilot-CSRF: 1` (a cross-site form cannot set headers; a cross-origin script can only send it after a CORS
preflight, which only `CORS_ORIGINS` pass) **and** reject any request whose `Origin` (or `Referer`) is not an allowed
origin or the API's own origin. `SameSite=Strict` is a third layer. CORS allows credentials for the explicit origins
only (`*` is refused by configuration). All other endpoints use the bearer header, which browsers never attach on
their own, so they are not exposed to CSRF.

### 4.5 Passwords

- **Hashing:** Argon2id via `argon2-cffi`, RFC 9106 low-memory profile (t=3, m=64 MiB, p=4), random salt per hash.
  Parameters live in each hash; `needs_rehash` upgrades old hashes at the next successful login. Hashing runs in a
  worker thread so it never blocks the event loop. Input is NFKC-normalised so the same passphrase typed on
  different systems matches.
- **Policy (NIST SP 800-63B style):** 12–256 characters, any characters including spaces and emoji, no composition
  rules, paste allowed; rejected if on a local list of common long passwords, made of one or two repeated
  characters, or built from the account's email, username or name. Never truncated: over 256 characters is an
  error. The common-password list is small and local; there is no breached-password (HIBP) lookup.
- **Change:** requires the current password; revokes every session (including the current one), invalidates reset
  tokens, then starts a fresh session for the calling browser.
- **Reset:** 256-bit single-use token, stored as a digest, valid 30 minutes; issuing a new one invalidates older
  unused ones; completion revokes every session and clears any lockout. The link carries the token in the URL
  **fragment** (`/reset-password#token=…`), which browsers never send to servers, and the page removes it from the
  address bar immediately. Delivery: there is no email integration. `PasswordResetNotifier` is the delivery
  interface; the default `NoDeliveryNotifier` delivers nothing (the request is still audited), tests use a capturing
  notifier, and administrators create links with `POST /users/{id}/password-reset`, which returns the link **once to
  the administrator only** (`Cache-Control: no-store`) for hand-over. That admin endpoint can be switched off with
  `PASSWORD_RESET_ADMIN_LINKS_ENABLED=false`. Self-service `/forgot-password` never returns a token.

### 4.6 Login protection

1. **Rate limits** (`RateLimiter` interface, in-memory sliding window): per client IP for login, forgot-password and
   reset-password (30 per 5 minutes), per email for login and forgot-password (10 per 5 minutes; also applies to
   unknown emails, so it reveals nothing), per IP for refresh (300 per 5 minutes), per user for password change.
   Exceeding one returns `429` with `Retry-After`.
2. **Account lockout:** consecutive failures are counted on the user row (under a row lock). From the 5th, the account
   is locked for 60 s, doubling per further failure, capped at 15 minutes. Locks always expire, so an attacker can
   delay but never permanently lock out a user; an administrator can clear a lock (Activate). Success resets the
   counter.
3. **No account enumeration:** unknown email, malformed email, wrong password, locked and inactive accounts all
   return the same `401 INVALID_CREDENTIALS` "Invalid email or password." after the same Argon2 work (a dummy
   verification for unknown accounts). The inactive check runs only after a correct password. Forgot-password
   always returns the same `202` message.

The in-memory limiter is correct for the supported deployment (one backend process, as in the Docker image). With
several workers or replicas each keeps its own counters (effective limits multiply); a Redis-backed `RateLimiter`
can be dropped in without touching the routes. The client IP is the direct peer: behind a reverse proxy, configure
uvicorn `--forwarded-allow-ips`, otherwise all users share the proxy's IP budget.

### 4.7 Frontend authentication lifecycle

- `AuthSession` (`src/services/authSession.ts`) holds the access token and user **in memory only** and connects
  itself to the shared `ApiClient`: it supplies the `Authorization` header and handles 401s.
- **Start-up:** `AuthProvider` calls `/auth/refresh` once (cookie → new access token). Until it answers, protected
  routes show "Checking your session"; on failure they redirect to `/login` (remembering the requested page).
- **Expired access token:** an API call answers 401 → one shared refresh → the call is retried **once**. Concurrent
  401s share a single in-flight refresh, so the token rotates once; a `409 REFRESH_CONFLICT` is retried once after a
  short delay. A failed refresh ends the session and returns the user to `/login` with "Your session has ended".
  There is no path that retries more than once.
- **Sign-out:** `/auth/logout`, then the TanStack Query cache is cleared so no user data outlives the session;
  other tabs are told through a `BroadcastChannel`. **Sign out everywhere** calls `/auth/logout-all`.
- Route guards (`RequireAuth`, `RequirePermission`) and the `permissions` list from `/auth/me` only shape the UI.
  They are not a security boundary: every endpoint enforces authentication and permissions on the server.
- Tokens are never written to `localStorage`, `sessionStorage` or readable cookies, and never logged.

### 4.8 Authorization entry point

Routes declare what they need: `Depends(require_permission(Permission.USERS_MANAGE))`. Nothing else checks
`is_superuser`. The decision is made by the `AuthorizationPolicy` returned by `get_authorization_policy()`:

- With a database configured, this is `EngineAuthorizationPolicy` (Module 04, section 5): permissions come from
  roles and permission assignments stored in the database.
- Without a database (Module 01 mode), `BootstrapAuthorizationPolicy` remains: the installation administrator
  (`is_superuser`) holds every system permission and other users hold none.
- `is_superuser` means "installation owner created by bootstrap", a break-glass account that always holds the
  system permissions (never data access). It is not a role.
- The settings API (Module 02) requires `settings:read` / `settings:manage`.

### 4.9 Account rules

- `is_superuser` can be set only by the bootstrap CLI. Write schemas use `extra="forbid"`, so a request that includes
  `is_superuser` (or any unknown field) is rejected with 422 instead of being silently ignored.
- An administrator cannot deactivate or delete their own account or the installation administrator.
- Deactivation and deletion revoke all sessions in the same transaction; deletion is soft (`deleted_at`), keeping
  history and foreign keys; the email becomes reusable.
- Changing a user's email resets `is_verified`.

### 4.10 Administrator bootstrap

`python -m app.cli create-admin` (interactive with hidden password prompts, or `--no-input` with `OPSPILOT_ADMIN_*`
environment variables). It takes a PostgreSQL advisory lock, refuses if any superuser row has ever existed, validates
the password policy, and records `admin_bootstrapped`. There is no HTTP route that creates administrators.
`python -m app.cli reset-password` is the host-level recovery path. Shell access to the server is the trust boundary.

### 4.11 Audit events

`AuditService.record` writes an `auth_events` row in the caller's transaction (so an event exists exactly when the
action was committed) and a structured log line. Recorded: `login_succeeded`, `login_failed` (with reason),
`logout`, `logout_all`, `password_changed`, `password_reset_requested`, `password_reset_completed`,
`account_created`, `account_updated`, `account_activated`, `account_deactivated`, `account_deleted`,
`sessions_revoked`, `refresh_token_reuse`, `admin_bootstrapped`. `details` accepts only simple values and refuses
secret-looking keys; attempted email addresses of unknown accounts are not stored. There is no API to read events
yet (Module 16).

## 5. Roles and permissions (Module 04)

### 5.1 Overview

Access is defined in the database, never in code: administrators create roles and permissions at runtime, assign
permissions to roles, and assign roles (or individual permissions) to users. There are no hard-coded business roles
such as "CEO" or "Manager". Everything is single-installation; there is no tenant column.

```
app/authorization/policy.py        policy format: validation and normalisation (pure, no I/O)
app/authorization/engine.py        AuthorizationEngine: resolve grants, evaluate requests, AI context
app/authorization/cache.py         per-process LRU of resolved grants, validated by a revision number
app/authorization/system_catalog.py  built-in permissions and the Administrator role (idempotent repair)
app/services/rbac_service.py       every management rule, audit event and revision bump
app/repositories/rbac_repository.py  queries (bulk grant loading, catalog search, counts)
app/api/routes/roles.py, permissions.py, access.py
```

### 5.2 Data model

| Table | Purpose |
| --- | --- |
| `roles` | `key` (immutable), `name`, `description`, `is_system`, `is_active`, soft delete (`deleted_at`) |
| `permissions` | `key`, `name`, `resource_type`, `action`, `policy_definition` (JSONB, normalized), `is_system`, `is_active`, `expires_at`, soft delete |
| `role_permissions` | role ↔ permission, with `assigned_by`, `revoked_at`, `revoked_by` (history is kept) |
| `user_roles` | user ↔ role, with `expires_at` and revocation history |
| `user_permissions` | direct grant or deny (`assignment_type`), optional narrowing `policy_override`, `expires_at`, revocation history |
| `rbac_audit_events` | who changed what, with before/after details |
| `authorization_state` | a single row holding the policy `revision` |

Keys are unique among live rows (partial unique indexes on `deleted_at IS NULL`), so a deleted role's key can be
reused; assignments are unique among unrevoked rows. Migration `0003` creates the tables and seeds the ten system
permissions, the **Administrator** system role holding all of them, and revision 1.

### 5.3 System permissions and the Administrator role

| Delegable (any role may hold them) | Administrator only |
| --- | --- |
| `users:read`, `settings:read`, `roles:read`, `permissions:read`, `access:review` | `users:manage`, `settings:manage`, `roles:manage`, `permissions:manage`, `access:assign` |

Administrator-only permissions count only through the built-in Administrator role (or the installation superuser);
they cannot be added to another role or granted directly, so nobody can build a "half administrator" that escalates
itself. System roles and permissions cannot be renamed, deactivated, deleted or have their permissions changed.
`python -m app.cli sync-permissions` restores them if a database was edited by hand.

Administrators get **no implicit data access**: reading business data always needs an explicit `database_data`
permission.

### 5.4 Policies and evaluation

Permission policies are structured JSON (format and examples: [docs/PERMISSION_POLICY.md](docs/PERMISSION_POLICY.md)).
`validate_policy` checks shape, identifiers, operators per value type, nesting depth and condition count, column
references and contradictions, and stores the normalized form. Schema-aware checks go through a `SchemaCatalog`
protocol; until schema registration exists, `UnknownSchemaCatalog` skips them.

`AuthorizationEngine` resolves a user's grants with three queries (roles, role grants, direct grants) for any
number of users, then evaluates in memory: inactive user → deny; no grant → deny; expired, revoked or inactive grants
ignored; direct deny removes a permission key; allow grants combined per table; deny policies applied last and
always win. The decision (`AuthorizationDecision`) is structured data: tables, columns, denied columns, operations,
row filters as condition trees, export and SQL-view flags. It never contains SQL.

### 5.5 Caching and invalidation

Every RBAC change, and every user activation, deactivation or deletion, increments `authorization_state.revision`
in the same transaction. `AuthorizationCache` stores resolved grants per user keyed by revision; each check reads the
current revision (one primary-key lookup), so a change made by any process is seen by the next request. Expiry is
evaluated at decision time, not at cache time. The cache is bounded (`AUTHORIZATION_CACHE_SIZE`, 0 disables it).

### 5.6 Management rules

- Every endpoint requires a system permission: `roles:read`/`roles:manage`, `permissions:read`/`permissions:manage`,
  `access:review` (read a user's access) and `access:assign` (change it).
- Nobody except the installation superuser can change their own roles or permissions (no self-assignment).
- The last active administrator cannot lose the Administrator role (defence in depth: the installation superuser
  also always counts as an administrator).
- A role still assigned to users cannot be deleted; deactivate it or remove it from the users first.
- Every change writes an `rbac_audit_events` row in the same transaction (`role_created`, `role_updated`,
  `role_activated`, `role_deactivated`, `role_deleted`, `permission_*`, `role_permission_assigned`/`revoked`,
  `user_role_assigned`/`revoked`, `user_permission_assigned`/`revoked`).

### 5.7 API

| Method and path | Permission |
| --- | --- |
| `GET/POST /roles`, `GET/PATCH/DELETE /roles/{id}` | `roles:read` / `roles:manage` |
| `POST/PUT /roles/{id}/permissions`, `DELETE /roles/{id}/permissions/{permission_id}` | `roles:manage` |
| `GET/POST /permissions`, `GET/PATCH/DELETE /permissions/{id}` | `permissions:read` / `permissions:manage` |
| `POST /permissions/validate-policy` | `permissions:manage` |
| `GET /users/{id}/roles`, `GET /users/{id}/permissions`, `GET /users/{id}/effective-permissions` | `access:review` |
| `POST/DELETE /users/{id}/roles[/{role_id}]`, `POST/DELETE /users/{id}/permissions[/{permission_id}]` | `access:assign` |
| `GET /authorization/me`, `GET /authorization/me/ai-context` | signed in (own data only) |
| `POST /authorization/check` | signed in for yourself; `access:review` for another user (diagnostic: marked `diagnostic: true`) |

### 5.8 AI authorization context

`AuthorizationEngine.ai_authorization_context(user)` returns what a later AI module may use to plan queries: roles,
permission keys, allowed resources and actions, per-table columns, operations and row filters, and export/SQL-view
flags, stamped with the policy revision and `is_authorization_token: false`. It excludes system permissions,
credentials, tokens, session data and email addresses. It is **context, not authorization**: every generated query
must still be checked by the engine (and later by the SQL security pipeline) at execution time.

### 5.9 Frontend

`PermissionsProvider` loads `GET /authorization/me` (keyed by user id, refetched on focus, every 5 minutes and after
any access change made in the app) and exposes `usePermissions()` with `hasPermission`, `canAccessResource` and
`effectivePermissions`. Until it answers, the permissions from the sign-in response are used. Navigation, routes
(`RequirePermission`) and buttons use it only to decide what to show; the server enforces every request.

Pages: **Roles** (list, search, filter, create, activate/deactivate, delete), **Role** (details, permission editor
with a review of additions and removals before saving, users, history), **Permissions** (list, search, filter by
resource and status), **Permission** (form-based policy builder with nested AND/OR groups, a review step validated by
the server with a plain-language summary and read-only JSON), and an access section on each **User** page (roles,
direct grants and denials with optional expiry, effective access).

## 6. Frontend foundation and design system (Module 05)

The full reference is [docs/FRONTEND_DESIGN_SYSTEM.md](docs/FRONTEND_DESIGN_SYSTEM.md). In short:

- **Tokens.** `src/styles/tokens.css` defines every colour, radius, shadow, type size, spacing name, breakpoint,
  motion value and z-index layer as CSS custom properties per theme, exposed to Tailwind 4 through `@theme inline`.
  Tailwind's default palette is removed, so components can only use semantic colours (`bg-surface`, `text-fg-muted`,
  `bg-accent`, `text-danger-text`). `src/design/tokens.ts` mirrors what code reads.
- **Themes.** Light and dark are two sets of variable values switched by a `dark` class on `<html>`
  (`ThemeProvider`; preference light/dark/system stored in `localStorage`, applied before paint by
  `public/theme-init.js`).
- **Components.** `src/components/ui/` holds the typed component library (forms, layout, navigation, feedback,
  overlays, data). Overlays share one focus-trap / Escape / scroll-lock hook.
- **Layouts.** Public, authentication, application (`AppLayout`: sidebar, drawer, top bar, breadcrumbs, error and
  loading boundaries) and administration (`AdminLayout`) layouts are route elements in `src/app/routes.tsx`, composed
  with the existing `RequireAuth`, `RedirectIfAuthenticated` and `RequirePermission` guards.
- **Navigation registry.** `src/app/navigation.ts` lists sections and items with an optional permission key; the
  sidebar, mobile drawer and administration gate are built from it. Breadcrumbs come from route `handle.crumb`.
- **Forms.** React Hook Form + Zod with `FormField` wiring labels, descriptions and errors for assistive technology.
- **Code splitting.** Administration pages load on demand; vendor code is split into `react`, `data` and `icons`
  chunks.

The interface only decides what to show. Authentication tokens are handled exactly as in section 4.7, and every
permission is enforced by the backend.

## 7. External schema registry (Module 06)

### 7.1 Purpose and boundary

The registry stores **metadata** about the company's business databases (schema sources, tables, columns,
relationships, a business glossary) so that later modules can plan queries. It stores no credentials, opens no
connections and never executes anything from imported metadata. Names are plain identifiers and data types come from
a fixed vocabulary (`app/schema_registry/data_types.py`), so text from an import cannot become SQL later.

### 7.2 Data model

| Table | Holds | Notable constraints |
| --- | --- | --- |
| `schema_sources` | One registered database: name, description, `database_type`, `dialect`, `schema_version`, `metadata_updated_at`, `business_glossary` (JSONB), `is_active`, `created_by` | Unique `lower(name)` among live rows; `database_type` check; version ≥ 1 |
| `schema_tables` | `schema_name`, `table_name`, description, business purpose, `is_active` | Unique `(source, lower(schema_name), lower(table_name))` among live rows; identifier checks |
| `schema_columns` | `column_name`, normalised `data_type`, description, business meaning, PK/FK/nullable/sensitive/active flags, `ordinal_position` | Unique `lower(column_name)` per table among live rows; a primary key cannot be nullable |
| `schema_relationships` | Source and target table and column, `relationship_type`, description, `is_active` | Type check; source column ≠ target column; unique pair among live rows |
| `schema_change_events` | Append-only history: event type, entity, `schema_version`, `version_incremented`, summary, structured details, actor | Never updated or deleted |

Sources, tables, columns and relationships use the soft-delete mixin: deleting keeps the row (with `deleted_at`) and
its history, and uniqueness applies only to live rows, so a name can be registered again. Migration
`0004_create_schema_registry_tables` creates the tables and seeds the seven `schema.*` permissions, granted to the
Administrator role.

### 7.3 Layers

- `app/schema_registry/` holds the pure logic, with no database access: `validation.py` (`validate_schema_state`
  checks a whole schema state and returns structured issues), `importer.py` (`plan_import`), `representation.py`
  (`build_representation`), `data_types.py`, `json_limits.py` (byte and depth limits for the raw body) and
  `catalog.py` (the policy `SchemaCatalog`).
- `SchemaRegistryRepository` classes query and flush; `SchemaRegistryService` owns transactions. Each write locks the
  source row (`SELECT … FOR UPDATE`), builds the schema state before and after the change, validates it, and refuses
  the change only for **errors the change introduces** (`422 SCHEMA_VALIDATION_FAILED` with the issues as details).
  Pre-existing problems and warnings never block an unrelated edit.
- Routes in `app/api/routes/schema_registry.py` are thin: parse, check the permission, call the service.

### 7.4 Versioning and history

Every change that alters the metadata a query planner would see (tables, columns, relationships, `database_type`,
`dialect`, glossary) increases `schema_version` by exactly one and writes one change event with a human-readable
summary and a field-level `from`/`to` diff. Renaming the source, editing its description or toggling `is_active` is
recorded without a version bump. Reads never change the version. Exports are recorded (without a bump) so the history
shows who took the metadata out. An import is one version and one event, however many items it changes.

### 7.5 Referential protection

A table or column used by a relationship cannot be deleted (`409 IN_USE`, details list the relationship ids) unless the
request asks for `cascade_relationships=true`, in which case the relationships are deleted in the same transaction and
recorded. Deleting a source deletes its tables, columns and relationships with it.

### 7.6 JSON import and export

The format is documented in [docs/SCHEMA_IMPORT_FORMAT.md](docs/SCHEMA_IMPORT_FORMAT.md). The route reads the raw body
with a byte limit, parses it with a nesting-depth limit, validates the envelope, then validates the document with
Pydantic (`extra="forbid"` everywhere). `plan_import` compares the document with the current state and produces the
list of creates, updates and removals for the chosen strategy (`create_only`, `merge`, `replace`). The planned state is
validated like any other change. A dry run returns the plan; a real import applies it in one transaction (tables, then
columns, then relationships) or not at all. `expected_schema_version` turns a stale preview into `409
SCHEMA_VERSION_CONFLICT`, and `replace` requires `confirm_replace`. Export writes the same format.

### 7.7 AI-ready representation

`GET /schemas/{id}/representation` returns a structured, deterministic description of the active metadata: tables
with their columns (name, type, nullability, keys, description, business meaning), relationships, and business terms,
stamped with `schema_version`. It leaves out inactive items, relationships whose ends are inactive, sensitive columns
(unless `include_sensitive=true`), and every internal field (ids, timestamps, users). Filters: `schema_name`, `table`
(with `include_related` to add directly related tables), `include_descriptions`, `include_business_terms`. It is input
for a later planning module, not a prompt; there are no embeddings and no retrieval.

### 7.8 Permissions

Seven system permissions, all delegable to any role through Module 04: `schema.read` (every read endpoint except
export), `schema.create`, `schema.update` (source, tables and columns), `schema.delete`, `schema.import`,
`schema.export` and `schema.relationship.manage`. No role names are checked anywhere.

Module 04's permission policies name tables and columns. `RegistrySchemaCatalog` implements its `SchemaCatalog`
protocol from the active tables and columns of active sources: once at least one is registered, a policy that names an unknown table or
column, or compares a column with a value of the wrong type, is rejected when it is saved. With nothing registered
the catalog is empty and policies are accepted as before.

### 7.9 Frontend

**Schemas** (`/admin/schemas`, needs `schema.read`): list with search, database-type and status filters, sorting,
pagination and real counts; register, edit and delete dialogs. **Schema** (`/admin/schemas/:id`) has tabs:
*Overview* (counts, details, live metadata checks, glossary), *Tables* (table list with filters and a column explorer
with PK, FK, sensitive and inactive markers; add, edit and delete tables and columns, with a second confirmation
when relationships would be deleted too), *Relationships* (list and a dialog that checks the draft before sending it:
required fields, inactive ends, self-reference, type compatibility, duplicates in either direction),
*Import / Export* (file selection, local JSON and size check, strategy, server-side preview with counts, changes,
errors and warnings, confirmation, result summary, JSON download), and *Schema History*. Every action is shown only
with its permission; the server enforces each request.

## 8. Company GET API connector (Module 07)

The full operator guide (configuration example, response contract, encryption and rotation, SSRF deployment
requirements, GET query-string risks) is [docs/CONNECTORS.md](docs/CONNECTORS.md). This section covers the design.

### 8.1 Purpose and boundary

OpsPilot does not connect to the company's business database and stores no database credentials. The company
exposes an HTTPS GET endpoint that takes SQL as a query parameter and returns JSON. A connector stores how to
call it, and the backend calls it with HTTPX. No public endpoint accepts SQL: the connection test takes no body
and sends a fixed statement, and execution is reachable only in-process through a sealed `ValidatedQueryRequest`
(8.6). SQL generation and the SQL security guard (section 12) are later modules.

### 8.2 Data model

- `external_api_connectors` (soft-deleted) holds:
  - the name, description and associated `schema_source_id`;
  - the base URL, endpoint path and `http_method`, with a check constraint that it is `'GET'`;
  - the SQL parameter name and extra query parameters;
  - the authentication type, the auth header name and `encrypted_credentials`;
  - `custom_headers` as JSONB (plain values, or ciphertext for secret headers);
  - the response contract, health check path, timeouts and size and SQL limits;
  - the active flag and the last test status, time, error category and summary.
- Database constraints enforce:
  - credentials present exactly when the auth type needs them, and always starting with `v1:`;
  - timeout ranges;
  - unique live names (case-insensitive) and unique live base URL + path;
  - **at most one active connector per schema source**.
- `connector_audit_events` is append-only. It records the operation, status, error category and HTTP status,
  the duration, the SQL fingerprint and length, the requesting user, and the execution and validation references.
  It never holds credentials or SQL text.

### 8.3 Layers

```
api/routes/connectors.py           thin handlers, require_permission(connector.*)
services/connector_service.py      CRUD, merge-on-update, test workflow, execute(), key rotation, audit
repositories/connector_repository  queries, filters, row locks
connectors/
  security/credentials.py          AES-256-GCM CredentialCipher (primary + previous keys)
  security/destinations.py         DestinationPolicy: URL, allowlist, DNS and address checks
  validators/config.py             paths, parameter and header names, values, field names
  adapters/http_client.py          shared HTTPX AsyncClient with IP pinning, limits and error mapping
  adapters/response.py             ResponseContract → NormalizedResult
  adapters/execution.py            ConnectorExecutionAdapter: verifies trusted requests, builds and sends the GET
  trusted.py                       ValidatedQueryRequest and ExecutionApprovalAuthority
  errors.py                        ErrorCategory and ConnectorExecutionError
```

### 8.4 HTTP client lifecycle

- One `ConnectorHttpClient` per backend process. It is created in the FastAPI lifespan, stored on `app.state` and
  closed on shutdown, and it pools connections (`CONNECTOR_POOL_MAX_CONNECTIONS` and `CONNECTOR_POOL_MAX_KEEPALIVE`).
- The client is configured with:
  - `trust_env=False`, so proxy environment variables are ignored;
  - `follow_redirects=False`;
  - a cookie policy that refuses every cookie;
  - certificate verification from certifi plus the optional `CONNECTOR_CA_BUNDLE`, with TLS 1.2 or newer.
- Each request has connect, read and overall timeouts, and the overall timeout is enforced with
  `asyncio.timeout`.
- The body is streamed and stops at the size limit.
- Requests go to the validated IP, with the original `Host` header and the SNI host name (8.5).
- Service methods commit their database transaction before any network call, so no row lock or pooled database
  connection is held while the company API is called.

### 8.5 Destination policy (SSRF)

The policy is enforced at save time (static checks) and again on every request (DNS):

- **Scheme:** HTTPS only. HTTP is allowed only in development or test with `CONNECTOR_ALLOW_HTTP`.
- **Hosts:** the host must be in `CONNECTOR_ALLOWED_HOSTS`, which denies everything when it is empty.
- **Addresses:** every resolved address must pass.
  - Private ranges need `CONNECTOR_ALLOWED_PRIVATE_NETWORKS`.
  - Loopback is allowed only in development or test.
  - Link-local, multicast, reserved and unspecified addresses are always refused.
- **DNS rebinding:** the connection is pinned to the validated address.

A connector whose destination no longer passes, for example after the allowlist changes, shows the problem on its
page and cannot be tested or used.

### 8.6 Trusted execution boundary

- `ValidatedQueryRequest` is a frozen, strict Pydantic model. It carries the connector, schema source and version,
  the requesting user, the validation and permission references, the issuer, the approval time and the exact SQL.
- `ExecutionApprovalAuthority` seals each request with HMAC-SHA256 under a random key that exists only in the running
  process. Requests are not serialisable across processes.
- Before anything is sent, the adapter rejects a request when:
  - the seal is missing or does not match;
  - the issuer is unknown;
  - the request is expired (older than 120 s);
  - the connector, schema source or version does not match;
  - the connector is inactive (except for the connection test);
  - the SQL is empty or contains NUL;
  - the SQL is over the length limit (`request_too_large`).
- The only issuer today is `connector_test`, and it may seal only `SELECT 1 AS opspilot_connection_test`. The SQL
  security guard module will register its own issuer.
- `ConnectorService.execute()` audits every attempt, including rejected ones, with a SHA-256 fingerprint of the SQL.

### 8.7 Credential encryption

- AES-256-GCM, with a random nonce per value and associated data `opspilot:connector:<id>:<purpose>`.
- Stored values use the format `v1:<key id>:<nonce>:<ciphertext>`.
- The key is `CONNECTOR_ENCRYPTION_KEY`, distinct from `SECRET_KEY`. Previous keys stay decrypt-only until
  `python -m app.cli rotate-connector-keys` re-encrypts everything.
- If the key is missing, operations that need it fail with 503. Nothing is stored in plaintext.
- Responses expose only `has_credentials` and a fixed mask. Updates keep a credential or secret header value when
  it is omitted.

### 8.8 Permissions and frontend

- Permissions are `connector.read`, `create`, `update`, `delete`, `test` and `activate`. They are seeded by
  migration 0005, granted to Administrator, and enforced through the Module 04 engine.
- **Connectors** (`/admin/connectors`, needs `connector.read`) has search, status, authentication and last-test
  filters, and pagination.
- The create and edit dialog:
  - masks secret fields and never prefills credentials;
  - shows an "empty keeps the stored value" note for each stored secret;
  - shows the server's approved hosts;
  - shows a warning about SQL travelling in the URL.
- The **Connector** page shows the masked configuration, connection status, the last test, a step-by-step test
  result, Activate/Deactivate, Edit, Delete with confirmation, and the audit activity. Each action appears only with
  its permission.

## 9. AI provider integration and LLM service layer (Module 08)

The full guide (NVIDIA setup, variables, error table, adding a provider, testing) is
[docs/AI_PROVIDERS.md](docs/AI_PROVIDERS.md). This section covers the design.

### 9.1 Boundary

Every call to a language model goes through `LLMService` (`app/ai/services/llm_service.py`). Application modules
depend on `LLMServiceDep` and on the provider-independent schemas (`LLMMessage`, `LLMGenerationRequest`,
`LLMGenerationResponse`); they never import a provider, a vendor SDK or HTTPX. Module 08 adds no SQL generation, SQL
execution, retrieval, embeddings or chat. No endpoint accepts prompt text.

### 9.2 Layers

```
app/api/routes/ai.py                 thin admin handlers, require_permission(ai.*)
app/ai/services/provider_admin_service.py   configuration views, health, fixed test prompt, audit, usage page
app/ai/services/llm_service.py       selection, validation, defaults/ceilings, bounded retries, latency, logging, usage
app/ai/services/usage_service.py     ai_usage_records writes (own transaction) and queries
app/ai/providers/base.py             LLMProvider: generate(), health_check()
app/ai/providers/openai_compatible.py  HTTPX adapter: POST /chat/completions, GET /models, error mapping, normalisation
app/ai/providers/nvidia.py           NVIDIA = OpenAI-compatible adapter with NVIDIA's name
app/ai/providers/registry.py         name -> implementation
app/ai/providers/http.py             the one pooled httpx.AsyncClient (lifespan-managed)
app/ai/config/provider_config.py     ProviderConfigService: Settings -> ProviderConfig per provider
app/ai/exceptions/errors.py          typed ProviderError hierarchy (AppError subclasses, fixed safe messages)
```

### 9.3 Configuration and credentials

There is one configuration system: `Settings` (`app/core/config.py`). `ProviderConfigService` turns it into a
`ProviderConfig` per provider (name, model, base URL, credential reference, temperature, max output tokens, timeout,
retry limits, active flag, missing settings) without reading the environment itself. API keys exist only as
`SecretStr` in the server environment; they are not stored in the database (so nothing is stored in plaintext), are
excluded from `repr`, are never returned (responses show `credential.configured` and the variable name) and are never
logged. Configuration changes are made in `.env` by whoever administers the host; no API changes them. With no key the
application starts and the provider reports `not_configured`. Storing provider credentials in the database later
would reuse the Module 07 AES-256-GCM `CredentialCipher`.

### 9.4 Request lifecycle

1. Validate and build an `LLMGenerationRequest` (422 `AI_REQUEST_INVALID` with locations only, never input).
2. Select the active provider; a missing key, model or HTTP client is `ProviderConfigurationError` (503).
3. End the caller's implicit database read transaction, so no pooled connection is held during the call.
4. Call the provider. On a retryable error (connection never established, 429, 502, 503), wait (exponential backoff
   with jitter, or the provider's `Retry-After`) and try again, up to `AI_MAX_RETRIES`, never waiting longer than
   `AI_RETRY_MAX_WAIT_SECONDS`. Authentication errors, invalid requests, malformed responses and timeouts after the
   request was sent are not retried.
5. Normalise the answer to `LLMGenerationResponse`, record one usage row (success or failure) and log the outcome.

### 9.5 Usage and audit

`ai_usage_records` (migration `0006`) holds one row per request: provider, model, operation, status, error
category, finish reason, provider-reported input/output/total tokens (NULL when not reported), latency, attempts,
OpsPilot request id, HTTP correlation id, provider request id, user. No prompt, answer, key or cost. Indexes serve the
newest-first list (`created_at`) and the provider filter (`provider, created_at`). Usage storage failures are logged
and do not fail the request. Running the provider test is also written to the audit trail as `ai_provider_tested`.

### 9.6 Permissions and frontend

`ai.provider.read`, `ai.provider.test` and `ai.usage.read` are delegable system permissions, seeded and granted to
Administrator by migration `0006`. **AI provider** (`/admin/ai`, shown with any of them) has a configuration card
(model, endpoint, "Set on server, hidden" key status), a health card, a connection-test card (fixed prompt, result,
latency, reported usage) and a usage table with totals. Each part appears only with its permission; the server
enforces every request.

## 10. Question understanding and business intent (Module 09)

The full guide (API, BusinessIntent, intent types, terminology, time rules, clarification rules, output contract,
samples) is [docs/QUESTION_UNDERSTANDING.md](docs/QUESTION_UNDERSTANDING.md). This section covers the design.

### 10.1 Boundary

Module 09 turns one question into a validated `BusinessIntent`. It reuses Module 08's `LLMService` (no new provider
code), Module 06's `SchemaRegistryService.outline()` (no second schema store) and Module 04's
`AuthorizationEngine` (`ai_authorization_context()` and `authorize()`; no second permission system). It generates
and executes no SQL, never calls the company connector, keeps no conversation memory and uses no embeddings, vector
store, RAG or agent framework. The model proposes; backend code decides.

### 10.2 Layers

```
app/api/routes/understanding.py        POST /ai/understand, POST /ai/understand/preview, GET /ai/understand/options
app/api/routes/business_terms.py       business term CRUD and schema-fields
app/ai/understanding/
  schemas/intent.py                    BusinessIntent and enums (strict Pydantic)
  schemas/llm_output.py                the model's JSON contract (strict, extra fields forbidden)
  schemas/terms.py, schemas/api.py     API contracts
  services/understanding_service.py    the pipeline (ARCHITECTURE 10.3), audit, logging
  services/schema_context.py           read-only view of the registered schema (one load per request)
  services/permission_view.py          the user's AI authorization context mapped onto registered tables
  services/schema_relevance.py         SchemaRelevanceSelector protocol + KeywordSchemaRelevanceSelector
  services/terminology_service.py      term CRUD, reference validation, term matching
  services/clarification.py            ClarificationEngine (priority rules, keys, no repeats)
  services/timezone_service.py         business timezone: setting -> environment -> UTC
  prompts/understanding_prompt.py      system prompt, context payload, correction messages
  validators/safety.py                 executable-content detection (SQL, code, markup, injection phrases)
  validators/intent_validator.py       semantic validation against schema, terms and permissions
  normalizers/time_resolver.py         deterministic date resolution
  normalizers/json_output.py, text.py  strict JSON extraction; question normalisation and tokens
app/models/business_term.py            BusinessTerm, QuestionUnderstandingEvent
```

### 10.3 Pipeline

Validate the question → load the schema → load the user's permissions (no data access: stop) → match active terms
(term on restricted data: safe denial) → select relevant permitted tables → prompt the model (temperature 0, JSON
only, `AI_UNDERSTAND_MAX_OUTPUT_TOKENS`) → parse strictly → validate semantically and resolve time → bounded
correction (`AI_UNDERSTAND_MAX_CORRECTIONS`, never for unsafe content or restricted fields) → clarification →
re-authorize tables, columns and operation with the permission engine → respond, audit, log. Only the model call is
non-deterministic; every other step is plain code with unit tests.

### 10.4 Schema relevance

`KeywordSchemaRelevanceSelector` scores only tables the user may see, with documented weights: table name (6),
tables of matched business terms (8), column names (3 each, up to 4), table description or business purpose words
and column description or business meaning words (1 each, up to 3), then adds directly related tables
(relationships) so join paths survive. When all permitted tables fit within `AI_UNDERSTAND_MAX_SCHEMA_TABLES`
they are all sent, best first (`all_tables`); otherwise the top-scoring ones are (`keyword`). The selector is behind
a protocol, so a future implementation (for example embeddings) can replace it without touching the pipeline.
The schema version is carried into the intent.

### 10.5 Terminology

`business_terms` (migration `0007`): one row per term per schema source, unique live name (case-insensitive), JSONB
synonyms and related tables/columns stored canonically, optional default aggregation and plain-language guidance,
active flag, soft delete, creator/updater. References are validated against the registered schema on write and
re-checked on read (`stale_references`). At question time, matching consumes the longest phrases first; a phrase
shared by several terms becomes an ambiguity. A term's registered aggregation and columns take precedence over the
model's choice.

### 10.6 Time behaviour

The model names the expression and its kind; `TimeResolver` computes the dates in the business timezone relative to
an explicit reference instant (never the server's local zone). Ranges are half-open `[start, end)`; ISO weeks;
calendar quarters; calendar periods are whole periods (`is_partial` when they contain today); rolling windows
`[tomorrow − N units, tomorrow)` include today and clamp month ends; named months/quarters without a year mean the
most recent started one; comparison periods are the previous period of the same kind or the same period last year.
The business timezone comes from the `business.timezone` application setting, else `BUSINESS_TIMEZONE`, else `UTC`,
and is validated as an IANA name. `tzdata` is a runtime dependency so this does not depend on the OS image.

### 10.7 Clarification

`ClarificationEngine` evaluates, in order: ambiguous term, missing definition, comparison without period, ranking
without measure, unclear request, and finally the model's own question (only when the backend cannot answer it).
Exactly one question is returned with a stable key; keys the client reports in `answered_clarifications` are never
asked again, and once all are exhausted the result is `unresolved_after_clarification`.

### 10.8 Permissions

The prompt contains only permitted tables and columns, never policies or row filters. The model's output is checked
against the same view; any restricted reference is blocking and reported generically. The final intent is
re-authorized with `AuthorizationEngine.authorize()` (`database_data`, operation `aggregate` or `read`).
`permission_context` keeps row filters and export flags for later modules and states `is_authorization_token:
false`: later modules must authorize again. The four new delegable permissions are `ai.question.understand`,
`ai.question.preview`, `business_term.read` and `business_term.manage`.

### 10.9 Observability

`question_understanding_events` (migration `0007`) stores one outcome row per interpretation (no question text,
prompt, output, credentials or policies), written in its own transaction so an audit failure never fails the
request. The log event `ai.understanding.completed` carries the same fields. Module 08 additionally records the model
call in `ai_usage_records` under the operation `question_understanding`.

### 10.10 Frontend

**Business terms** (`/admin/business-terms`, `business_term.read`): list with search and schema/kind/status filters,
stale-field warnings, and, with `business_term.manage`, create/edit dialog (schema, name, description, kind,
synonyms, fields chosen from the registered schema, default aggregation, guidance, active), activate/deactivate and
delete. **Question preview** (`/admin/question-preview`, `ai.question.preview`): choose a schema, enter a question and
see the status, intent, metrics, dimensions, filters, time, sorting, assumptions, ambiguities, clarification (with
"mark as answered"), validation issues, permission status and diagnostics; the business timezone is shown and, with
`settings:manage`, editable. Each preview is one model request and runs only on submit.

## 11. Planned modular backend architecture

The backend is a modular monolith. Each business module adds its own routes, schemas, services and models in
the existing layers:

```
app/
  api/routes/   thin HTTP handlers: validate input, call one service, return a schema
  services/     business logic, orchestration and transaction boundaries
  repositories/ data access (one repository per model)
  models/       SQLAlchemy models
  schemas/      Pydantic request/response contracts
  database/     Base, engine and sessions, transactions, health check
  core/         configuration, logging, exceptions, middleware, security primitives, authorization
  cli/          host-level administration commands
```

Planned modules (subject to the project owner's sequencing):

| Module | Adds |
| --- | --- |
| Authentication and users | **Implemented (Module 03, section 4)** |
| Roles and permissions | **Implemented (Module 04, section 5)** |
| Schema registry | **Implemented (Module 06, section 7)** |
| External API integration | **Implemented (Module 07, section 8)** |
| LLM integration | **Implemented (Module 08, section 9)** |
| Question understanding | **Implemented (Module 09, section 10)** |
| SQL generation | Question + relevant schemas → candidate SQL |
| SQL security guard | Independent validation and authorization of generated SQL |
| Conversations and answers | Chat history, natural-language answers, charts (Recharts) |

Rules for every module: route handlers stay thin, logic lives in services, persistence lives in repositories,
dependencies are injected through `app/api/deps.py`, and no module imports another module's routes.

## 12. Planned SQL security execution pipeline

Generated SQL is untrusted input. Before anything reaches the company API it passes a pipeline that is
independent of the LLM and cannot be skipped:

```
Question
  │
  ▼
1. Understand the question                (LLM, validated by the backend: Module 09)
2. Retrieve relevant registered schemas    (schema registry, filtered by the user's permissions)
3. Generate SQL                            (LLM)
  │
  ▼
4. SQL security guard                      (deterministic, no LLM)
   a. Parse SQL into a syntax tree; reject anything that does not parse
   b. Allow a single read-only SELECT statement only (no DML/DDL, no multiple statements, no comments tricks)
   c. Every referenced table and column must be registered and permitted for this user
   d. Block dangerous functions and system catalogs; enforce a row limit
   e. Record the decision and the final SQL in the audit log
  │  rejected → explain to the user, nothing is executed
  ▼
5. Execute through the company GET API     (HTTPX, timeouts, size limits)
6. Receive results
7. Generate the natural-language answer    (LLM, grounded only in the returned rows)
```

Security principles:

- The LLM never decides whether SQL is safe; the guard does, using deterministic rules.
- Authorization is checked against the requesting user's permissions, not the model's output.
- The company API is called only by the backend, only with guarded SQL, and its credentials never leave the
  backend.
- Every step is logged with the request ID, without logging secrets or result data unnecessarily.
