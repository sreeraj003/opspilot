# OpsPilot AI architecture

This document describes how OpsPilot AI is deployed and how its parts fit together. Sections marked
**planned** describe later modules; Modules 01 (foundation) and 02 (application database layer) are implemented.

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

The API client already supports a per-request headers provider, so the authentication module can add a bearer
token without changing callers.

## 3. Application database (Module 02)

OpsPilot's PostgreSQL database stores **OpsPilot's own data**, never the company's business data. The company's
database is reached only through its GET SQL API (section 4); OpsPilot never opens a connection to it.

- Implemented: `application_settings` (non-secret installation settings).
- **Planned:** users and roles, permissions, registered schema metadata, external API configuration,
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

## 4. External GET SQL API integration (planned)

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

## 5. Planned modular backend architecture

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
  core/         configuration, logging, exceptions, middleware
```

Planned modules (subject to the project owner's sequencing):

| Module | Adds |
| --- | --- |
| Authentication and users | Login, sessions or tokens, user management |
| Roles and permissions | Which users may query which registered tables and columns |
| Schema registry | Registering the company's table and column metadata used for SQL generation |
| External API integration | Configured client for the company's GET SQL endpoint |
| LLM integration | NVIDIA-hosted model client (`NVIDIA_BASE_URL`, `NVIDIA_MODEL`, `NVIDIA_API_KEY`) |
| SQL generation | Question + relevant schemas → candidate SQL |
| SQL security guard | Independent validation and authorization of generated SQL |
| Conversations and answers | Chat history, natural-language answers, charts (Recharts) |

Rules for every module: route handlers stay thin, logic lives in services, persistence lives in repositories,
dependencies are injected through `app/api/deps.py`, and no module imports another module's routes.

## 6. Planned SQL security execution pipeline

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
