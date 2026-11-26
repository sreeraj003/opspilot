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

## 8. External GET SQL API integration (planned)

OpsPilot does not connect to the company's business database. The company already has a backend endpoint that
accepts SQL as a query parameter and returns results:

```
GET https://company.example/api/query?sql=SELECT ...
```

OpsPilot will:

1. Store the endpoint URL and its credentials as installation configuration (secrets never reach the browser).
2. Call it from the backend with HTTPX, using timeouts, response-size limits and strict response parsing.
3. Send only SQL that has passed the security pipeline below. There will never be an endpoint that forwards
   arbitrary SQL from a client.

The company keeps full control: its API decides which database user runs the query and can add its own limits.

## 9. Planned modular backend architecture

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
| Schema registry | Registering the company's table and column metadata used for SQL generation |
| External API integration | Configured client for the company's GET SQL endpoint |
| LLM integration | NVIDIA-hosted model client (`NVIDIA_BASE_URL`, `NVIDIA_MODEL`, `NVIDIA_API_KEY`) |
| SQL generation | Question + relevant schemas → candidate SQL |
| SQL security guard | Independent validation and authorization of generated SQL |
| Conversations and answers | Chat history, natural-language answers, charts (Recharts) |

Rules for every module: route handlers stay thin, logic lives in services, persistence lives in repositories,
dependencies are injected through `app/api/deps.py`, and no module imports another module's routes.

## 10. Planned SQL security execution pipeline

Generated SQL is untrusted input. Before anything reaches the company API it passes a pipeline that is
independent of the LLM and cannot be skipped:

```
Question
  │
  ▼
1. Understand the question                (LLM)
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
