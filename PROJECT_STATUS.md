# OpsPilot AI: project status

## Current module

**Module 02: Application database architecture and persistence layer.** Implemented and verified on 2026-11-25.
Awaiting the project owner's review. Module 03 has not been started.

Module 01 (project foundation) is merged and unchanged in behaviour; its record is kept at the end of this file.

## Module 02: what was implemented

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

### Models and tables

| Model | Table | Columns |
| --- | --- | --- |
| `ApplicationSetting` | `application_settings` | `id uuid PK`, `key varchar(128) NOT NULL`, `value jsonb NOT NULL`, `description text NULL`, `created_at timestamptz NOT NULL`, `updated_at timestamptz NOT NULL` |

Constraints: `pk_application_settings`, `uq_application_settings_key` (also the lookup/ordering index),
`ck_application_settings_key_format`, `ck_application_settings_value_not_null`, `ck_application_settings_value_size`,
`ck_application_settings_description_length`. No foreign keys yet.

### Migrations

| Revision | File | Change |
| --- | --- | --- |
| `0001` | `backend/migrations/versions/2026_11_25_0001_create_application_settings.py` | Create `application_settings` (downgrade drops it) |

### API endpoints

| Method and path | Result |
| --- | --- |
| `GET /api/v1/settings?offset&limit` | `200` page `{items, total, offset, limit}` ordered by key (`limit` 1–100, default 50) |
| `GET /api/v1/settings/{key}` | `200` setting, `404 NOT_FOUND` |
| `PUT /api/v1/settings/{key}` | `201` created / `200` replaced, `409 CONFLICT` on a concurrent create, `422 VALIDATION_ERROR` |
| `DELETE /api/v1/settings/{key}` | `204`, `404 NOT_FOUND` |
| All of the above | `503 SERVICE_UNAVAILABLE` (not configured) or `503 DATABASE_UNAVAILABLE` (unreachable) |

### New configuration

`DATABASE_POOL_TIMEOUT_SECONDS` (10), `DATABASE_POOL_RECYCLE_SECONDS` (1800), now also passed through Compose with
`DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_CONNECT_TIMEOUT_SECONDS`. Tests only: `TEST_DATABASE_URL`,
and optionally `TEST_POSTGRES_USER` / `TEST_POSTGRES_PASSWORD` / `TEST_POSTGRES_PORT` for `docker-compose.test.yml`.
The existing `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` setup is unchanged.

### Dependencies

`SQLAlchemy[asyncio]==2.1.9` (adds greenlet 3.5.6, required for async), dev: `pytest-asyncio==1.5.0`.

### Files created

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

### Files modified

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

### Commands executed and results (Module 02)

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

## Remaining limitations

- `DELETE` has no "protected setting" rule yet: any existing setting can be deleted. Rules (and who may change
  settings) arrive with authentication and permissions.
- No authentication on the settings API yet (authentication is a later module). Do not expose the backend publicly
  before then.
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
- Recharts is listed in the planned stack but intentionally not installed yet (unused in Module 01).
- PostgreSQL keeps the password from its first start; changing `POSTGRES_PASSWORD` later requires changing it
  in the database or recreating the volume.
- `VITE_API_BASE_URL` is fixed at build time; changing it requires rebuilding the frontend image.

## Next module

Module 03, as defined by the project owner. Not started: waiting for review and approval of Module 02.
