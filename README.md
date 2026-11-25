# OpsPilot AI

OpsPilot AI is an AI-powered Business Intelligence Assistant. People in a company ask business questions in
natural language; OpsPilot turns each question into SQL, passes that SQL through an independent security and
authorization layer, runs approved queries through the company's own existing GET API, and answers in plain
language based on the real results.

Each company installs and runs its **own independent copy** of OpsPilot AI. It is not a multi-tenant SaaS
product, and it never connects directly to the company's business database.

> **Status:** Module 01 (project foundation) and Module 02 (application database layer) are implemented. Authentication, schema registration, the external
> API integration, the LLM, SQL generation, the SQL security guard and the chat interface arrive in later
> modules. See [PROJECT_STATUS.md](PROJECT_STATUS.md).

## Architecture overview

```
Browser ──> frontend (React SPA) ──HTTP/JSON──> backend (FastAPI) ──> application database (PostgreSQL)
                                                     │
                                                     └──(later modules)──> company GET SQL API, LLM provider
```

- **frontend/**: React + TypeScript single-page app. Talks to the backend only through one API client.
- **backend/**: FastAPI service. Owns configuration, logging, error handling and OpsPilot's own database.
- **Application database**: PostgreSQL holding OpsPilot's own data (users, registered schemas, settings,
  conversations in later modules). It is *not* the company's business database.

Details are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Technology stack

| Area | Technologies |
| --- | --- |
| Backend | Python 3.12+, FastAPI, Pydantic v2 + pydantic-settings, SQLAlchemy 2.x, Alembic, psycopg 3, Uvicorn, HTTPX (tests) |
| Frontend | React 19, TypeScript (strict), Vite, Tailwind CSS 4, React Router, TanStack Query, Lucide icons |
| Tests | Pytest + HTTPX (backend), Vitest + React Testing Library (frontend) |
| Infrastructure | Docker, Docker Compose, PostgreSQL 16, nginx (serves the built frontend) |

Recharts is part of the planned stack but is not installed yet: nothing in Module 01 draws a chart, and unused
dependencies are avoided. It will be added with the analytics module.

## Prerequisites

- **Docker route:** Docker Engine 24+ with Docker Compose v2.
- **Local route:** Python 3.12 or newer, Node.js 22 LTS with npm 10+, and a PostgreSQL 16 server (the
  Compose database works, see below).

## Environment setup

All configuration lives in one file at the repository root:

```bash
cp .env.example .env          # Windows: copy .env.example .env
```

Then edit `.env`:

- `POSTGRES_PASSWORD`: required for Docker Compose. Use URL-safe characters (it is embedded in a URL).
- `DATABASE_URL`: only for running the backend outside Docker.
- `SECRET_KEY`: required (32+ characters) when `APP_ENV=production`.
- `NVIDIA_*`: optional until the LLM module.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_NAME` | `OpsPilot AI` | Display name returned by the API |
| `APP_ENV` | `development` | `development`, `test`, `staging` or `production` |
| `APP_DEBUG` | `false` | Must be `false` in production |
| `APP_HOST` / `APP_PORT` | `0.0.0.0` / `8000` | Bind address for a locally started backend |
| `DATABASE_URL` | empty | PostgreSQL URL (`postgresql+psycopg://…`; `postgresql://` is accepted and converted). Required in production |
| `DATABASE_POOL_SIZE` / `DATABASE_MAX_OVERFLOW` | `5` / `10` | Connections kept open / extra connections allowed under load (per backend process) |
| `DATABASE_POOL_TIMEOUT_SECONDS` | `10` | Wait for a free connection before answering `503` |
| `DATABASE_POOL_RECYCLE_SECONDS` | `1800` | Replace connections older than this |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | `5` | Give up connecting after this |
| `TEST_DATABASE_URL` | empty | **Tests only.** A separate, disposable database for `pytest` (see [Database tests](#database-tests)). Never your real database |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated browser origins allowed to call the API. `*` is rejected |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `SECRET_KEY` | empty | Application secret, 32+ characters. Required in production |
| `NVIDIA_API_KEY` / `NVIDIA_MODEL` | empty | LLM provider credentials (optional for now) |
| `NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` | LLM provider endpoint (must be https) |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `opspilot` / — / `opspilot` | Docker Compose database credentials |
| `VITE_API_BASE_URL` | `http://localhost:8000` | Backend URL as seen from the browser (built into the frontend) |
| `VITE_API_TIMEOUT_MS` | `10000` | Frontend request timeout |

Invalid configuration stops the backend at startup with a message naming the setting. Error messages never
echo the values themselves, because they may be secrets.

## Running with Docker Compose

```bash
cp .env.example .env            # set POSTGRES_PASSWORD
docker compose up --build
```

| Service | URL | Notes |
| --- | --- | --- |
| frontend | <http://localhost:5173> | nginx serving the built app |
| backend | <http://localhost:8000> | API docs at <http://localhost:8000/docs> |
| db | not published | Only reachable on the internal `data` network |

The backend waits for a healthy database, applies Alembic migrations, then starts. The frontend waits for a
healthy backend. Database files live in the `postgres_data` volume, so they survive `docker compose down`
(`docker compose down -v` deletes them).

## Application database

OpsPilot's own PostgreSQL 16 database (never the company's business database). The backend uses async SQLAlchemy 2.x
with psycopg 3; schema changes are Alembic migrations. Design details: [ARCHITECTURE.md §3](ARCHITECTURE.md#3-application-database-module-02).

**Prerequisites:** a PostgreSQL 16 server and a user that owns the OpsPilot database. With Docker Compose this is
the `db` service and nothing else is needed. Give the application a database user without superuser rights.

### Running migrations

Docker Compose applies them automatically: the backend container runs `alembic upgrade head` on every start (it is a
no-op when the schema is current). For a backend started on your machine:

```bash
cd backend
alembic upgrade head       # apply all migrations (uses DATABASE_URL from .env)
alembic current            # show the applied revision
alembic history            # list revisions
alembic downgrade -1       # undo the latest migration (development only)
```

To run them inside the running Compose stack: `docker compose exec backend alembic current`.

### Creating a new migration

1. Add or change a model in `backend/app/models/` (new models must be imported in `app/models/__init__.py`).
2. Generate the revision against an up-to-date development database, with the next sequential id:

   ```bash
   cd backend
   alembic upgrade head
   alembic revision --autogenerate --rev-id 0002 -m "create users"
   ```

3. Read and tidy the generated file in `migrations/versions/` (autogenerate is a starting point, not the final
   word), make sure `downgrade()` reverses `upgrade()`, then run `alembic upgrade head` and the tests.

Never edit a migration that has already been applied anywhere; write a new one. `alembic check` reports models that
are out of sync with the migrations, and the test suite fails in that case too.

## Installing and running locally

Start only the database from Compose, published on `127.0.0.1:5432`:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db
```

and set `DATABASE_URL=postgresql+psycopg://opspilot:<POSTGRES_PASSWORD>@localhost:5432/opspilot` in `.env`.

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

The backend reads the root `.env` (and `backend/.env`, if present, which takes priority).

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Application name, description, version, environment |
| `GET /health` | Application health (`200` while the process serves requests) |
| `GET /health/database` | Runs `SELECT 1` through a database session: `200 ok`, or `503` with `unavailable` / `not_configured` |
| `GET /api/v1/settings?offset=0&limit=50` | List application settings (paginated, ordered by key, `limit` ≤ 100) |
| `GET /api/v1/settings/{key}` | One setting, or `404` |
| `PUT /api/v1/settings/{key}` | Create (`201`) or replace (`200`) a setting. Body: `{"value": <any JSON except null>, "description": "optional"}` |
| `DELETE /api/v1/settings/{key}` | Delete a setting (`204`), or `404` |
| `GET /docs`, `/redoc`, `/openapi.json` | OpenAPI documentation |

Health responses never include URLs, host names or credentials. Setting keys are lowercase (`a-z`, `0-9`, `.`, `_`,
`-`, starting with a letter, up to 128 characters). Settings are for non-secret configuration only: keys or JSON
members that look like credentials (`password`, `token`, `api_key`, ...) are rejected with `422`. When the database
is down, these endpoints return `503 DATABASE_UNAVAILABLE`.

Example:

```bash
curl -X PUT http://localhost:8000/api/v1/settings/ui.page_size \
     -H "Content-Type: application/json" -d '{"value": 25, "description": "Rows per page"}'
```

On **Windows without Docker**, psycopg's async mode needs the selector event loop. `uvicorn --reload` already uses
it; for a run without `--reload`, add `--loop asyncio:SelectorEventLoop`.

### Frontend

```bash
cd frontend
npm ci
npm run dev                        # http://localhost:5173
```

The home page shows live **System status** cards for the application, the backend API and the database: the
quickest way to confirm the frontend can reach the backend.

Other scripts: `npm run build`, `npm run preview`, `npm run typecheck`.

## Running tests

### Backend

```bash
cd backend
pytest
```

Without a test database, the PostgreSQL tests are **skipped** (reported as `s`); validation, configuration and
"database unavailable" behaviour still run.

#### Database tests

Database tests need a **separate, disposable** PostgreSQL database: they drop and recreate its tables. Do not point
them at your real database. `docker-compose.test.yml` starts one in memory on `127.0.0.1:55432`, with its own
credentials, as a separate Compose project that never touches the application database:

```bash
docker compose -f docker-compose.test.yml up -d --wait
```

Then set `TEST_DATABASE_URL`, either in `.env`:

```
TEST_DATABASE_URL=postgresql+psycopg://opspilot_test:opspilot_test@localhost:55432/opspilot_test
```

or for one shell (PowerShell: `$env:TEST_DATABASE_URL="postgresql+psycopg://opspilot_test:opspilot_test@localhost:55432/opspilot_test"`;
bash: `export TEST_DATABASE_URL=...`), and run:

```bash
cd backend
pytest                          # everything
pytest tests/database           # database layer only
pytest -m postgres              # only the tests that use PostgreSQL
```

Safety: pytest stops if the test database's name does not contain `test`, or if it is the same database as
`DATABASE_URL`. Each run rebuilds the test schema with the migrations; the migration test creates and drops its own
scratch database (the test user needs `CREATEDB`, which the test container's user has). Stop the container with
`docker compose -f docker-compose.test.yml down` (its data is discarded).

### Frontend

```bash
cd frontend
npm test                           # Vitest + React Testing Library
npm run typecheck
```

## Error format

Every failed request returns the same envelope and an `X-Request-ID` header:

```json
{
  "success": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "The requested resource was not found.",
    "request_id": "3f6c0f1e9b5d4f7f8a2b1c0d9e8f7a6b"
  }
}
```

Unexpected exceptions return `500 INTERNAL_SERVER_ERROR` with a generic message; the traceback goes to the logs
only. Logs are JSON lines with `timestamp`, `level`, `module`, `message` and `request_id`. Fields that look like
secrets (`password`, `token`, `api_key`, `authorization`, `database_url`, …) are redacted.

## Project layout

```
backend/
  app/
    api/            deps.py (dependency providers), router.py, routes/ (thin HTTP handlers)
    core/           config.py, logging.py, exceptions.py, middleware.py (request correlation)
    database/       base.py (Base), naming.py, session.py (engine, sessions), transaction.py, health.py
    models/         SQLAlchemy models (base_model.py mixins, application_setting.py)
    repositories/   Data access (base_repository.py, application_setting_repository.py)
    schemas/        Pydantic request/response contracts
    services/       Business logic and transaction boundaries (health, application settings)
    main.py         Application factory and ASGI app
  migrations/       Alembic environment and versions/ (0001: application_settings)
  tests/            API and config tests; tests/database/ for the database layer
frontend/
  src/
    app/            App, providers, routes, query client
    components/     Reusable UI (status cards, badges, error boundary)
    layouts/        AppShell
    pages/          HomePage, NotFoundPage
    services/       API client, environment config, typed endpoint calls
    hooks/          API client injection, status queries, document title
    types/          API contract types
    utils/, styles/
docker-compose.yml, docker-compose.dev.yml, docker-compose.test.yml, .env.example
```

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `required variable POSTGRES_PASSWORD is missing a value` | Create `.env` from `.env.example` and set `POSTGRES_PASSWORD`. |
| Backend exits with `Invalid production configuration` | In production set `SECRET_KEY` (32+ chars) and `DATABASE_URL`, and set `APP_DEBUG=false`. |
| `/health/database` returns `not_configured` | `DATABASE_URL` is empty for a locally started backend. |
| `/health/database` returns `unavailable` | PostgreSQL is not running or not reachable. Check `docker compose ps` and the password in `DATABASE_URL`. |
| Changed `POSTGRES_PASSWORD` and the backend can no longer log in | PostgreSQL keeps the password from the first start. Change it inside the database, or run `docker compose down -v` to recreate it (deletes data). |
| Settings endpoints return `500` and the log shows `sqlstate 42P01` | Migrations were not applied: run `alembic upgrade head`. |
| `Psycopg cannot use the 'ProactorEventLoop'` (Windows, no Docker) | Start uvicorn with `--reload`, or add `--loop asyncio:SelectorEventLoop`. |
| `pytest` exits with `TEST_DATABASE_URL must ...` | Point `TEST_DATABASE_URL` at a separate database whose name contains `test` (see Database tests). |
| Frontend shows **Unreachable** | The backend is down, `VITE_API_BASE_URL` is wrong, or the page origin is missing from `CORS_ORIGINS`. |
| Changed `VITE_API_BASE_URL` has no effect in Docker | It is built into the bundle: run `docker compose up --build frontend`. |
| Port 8000, 5173 or 5432 is busy | Set `BACKEND_PORT`, `FRONTEND_PORT` or `POSTGRES_PORT` in `.env` (update `CORS_ORIGINS` to match). |
| `pip`/`npm` fail with certificate errors during `docker compose build` | A TLS-intercepting corporate proxy is in the way. Add its CA to the build environment; do not disable verification. |

## Secrets

`.env` files are git-ignored; only `.env.example` is committed. Docker images contain no secrets: they receive
configuration at runtime. `VITE_*` values are compiled into browser JavaScript, so they must never hold secrets.
