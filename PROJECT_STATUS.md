# OpsPilot AI: project status

## Current module

**Module 01: Project foundation and application bootstrap.** Implemented and verified on 2026-11-25.
Awaiting the project owner's review. Module 02 has not been started.

### Note on earlier code

Before this module, the repository held an earlier Module 01 and a Module 03 (multi-tenant authentication,
organizations, RBAC, audit log). That design contradicts the current specification (single-company
installations, no authentication in Module 01). On the project owner's decision, this module rebuilds the
foundation from scratch and removes that code. It remains available in git history (commit `977a2e6`).

## Completed features

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

## Pending features (later modules)

Authentication, user management, roles and permissions, schema registration, external GET SQL API integration,
LLM integration, SQL generation, SQL security guard, query execution, chat interface, analytics and charts
(Recharts will be installed then).

## Created files

```
.env.example                     ARCHITECTURE.md                PROJECT_STATUS.md
docker-compose.yml               docker-compose.dev.yml
backend/Dockerfile               backend/.dockerignore
backend/app/api/routes/system.py backend/app/core/middleware.py (moved from app/middleware/request_context.py)
backend/migrations/versions/.gitkeep
backend/tests/test_database_health.py  backend/tests/test_errors.py
backend/tests/test_migrations.py       backend/tests/test_system.py
frontend/Dockerfile  frontend/.dockerignore  frontend/nginx.conf
frontend/src/components/BrandMark.tsx  DetailList.tsx  StatusCard.tsx
frontend/src/components/SystemStatusPanel.tsx  SystemStatusPanel.test.tsx
frontend/src/hooks/apiClientContext.ts  useSystemStatus.ts
frontend/src/pages/HomePage.test.tsx  NotFoundPage.test.tsx
frontend/src/services/systemService.ts
```

## Modified files

```
README.md
backend/alembic.ini  requirements.txt  requirements-dev.txt  pytest.ini
backend/app/main.py  api/deps.py  api/router.py  api/routes/health.py
backend/app/core/config.py  core/exceptions.py  core/logging.py
backend/app/database/base.py  database/session.py
backend/app/schemas/errors.py  schemas/health.py  services/health_service.py
backend/migrations/env.py
backend/tests/conftest.py  test_config.py  test_logging.py
frontend/package.json  package-lock.json  index.html  vite.config.ts  public/favicon.svg
frontend/src/app/App.test.tsx  app/providers.tsx
frontend/src/components/ErrorBoundary.tsx  StatusBadge.tsx  layouts/AppShell.tsx
frontend/src/pages/HomePage.tsx  NotFoundPage.tsx
frontend/src/services/apiClient.ts  apiClient.test.ts  config.ts  config.test.ts
frontend/src/hooks/useApiClient.ts  styles/index.css  test/utils.tsx  types/api.ts  utils/format.ts
```

Removed: the earlier auth / RBAC / multi-tenant code (`app/models`, `app/repositories`, `app/security`,
auth/user/audit routes, services and schemas, `app/cli.py`, the auth migration and its tests),
`app/middleware/` and `app/utils/` (folded into `app/core`), `frontend/src/features/` (moved into
`components/`, `hooks/`, `services/`), and the per-app `.env.example` files (replaced by the root one).

## Dependencies

Backend runtime (`requirements.txt`): fastapi 0.165.0, starlette 1.10.0, uvicorn[standard] 0.54.0,
pydantic 2.14.4, pydantic-settings 2.16.3, SQLAlchemy 2.1.9, psycopg[binary] 3.3.9, alembic 1.20.1.
Backend dev: pytest 9.2.0, httpx 0.28.1.

Frontend: react / react-dom 19.3.1, react-router 8.5.2, @tanstack/react-query 5.108.3, lucide-react 1.70.0
(added). Dev: vite 8.3.8, typescript 6.0.3, tailwindcss + @tailwindcss/vite 4.3.5, vitest 5.1.1,
@testing-library/react 16.3.3, @testing-library/dom 10.4.2, @testing-library/jest-dom 8.0.0, jsdom 31.0.1.
Removed: @testing-library/user-event (unused).

## Commands executed and results

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

Module 02, as defined by the project owner. Not started: waiting for review and approval of Module 01.
