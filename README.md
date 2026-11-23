# OpsPilot AI

Enterprise operations intelligence and automation platform, built as a modular monolith:

- `backend/`: Python, FastAPI, Pydantic Settings, SQLAlchemy (API under `/api/v1`)
- `frontend/`: React, TypeScript, Vite, Tailwind CSS, React Router, TanStack Query

Both apps run independently. The frontend talks to the backend over HTTP using `VITE_API_BASE_URL`.

## Prerequisites

- Python 3.12 or newer (developed and tested on 3.13)
- Node.js 22 LTS or newer, npm 10+

## Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env               # Windows: copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

- Health: <http://localhost:8000/api/v1/health>
- Readiness: <http://localhost:8000/api/v1/ready>
- OpenAPI docs: <http://localhost:8000/api/v1/docs>

Run tests:

```bash
cd backend
pytest
```

### Configuration

All settings come from environment variables (or `backend/.env`). See [`backend/.env.example`](backend/.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | `development`, `test`, `staging` or `production` |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` | Structured JSON logs, or `console` for local reading |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated browser origins allowed to call the API |
| `DATABASE_URL` | empty | Optional in Module 01, e.g. `postgresql+psycopg://user:pass@localhost:5432/opspilot` |

### Health and readiness

- `GET /api/v1/health` returns `200` with service name, version, environment and timestamp while the process is serving.
- `GET /api/v1/ready` returns a list of checks. It returns `200` with `"status": "ready"` when every **required** check passes, otherwise `503` with `"status": "not_ready"`.
  - `application` is always checked.
  - `database`: when `DATABASE_URL` is empty it is reported as `not_configured` (not required, never shown as connected). When it is set, the API runs `SELECT 1`; failure makes the service not ready.

### Error format

Every error response uses one envelope, and every response carries an `X-Request-ID` header (a valid incoming one is propagated):

```json
{ "error": { "code": "not_found", "message": "…", "details": null, "request_id": "…" } }
```

Unhandled exceptions return a generic `500 internal_error`; details go only to the logs.

### Backend layout

```
backend/app/
  api/routes/     HTTP route handlers (thin; delegate to services)
  api/deps.py     Dependency-injection providers (settings, database, sessions, services)
  core/           Settings, logging, lifespan, exceptions, request context
  database/       Declarative base and engine/session management
  models/         ORM models (none yet)
  schemas/        Pydantic request/response contracts
  repositories/   Data access (added with the first models)
  services/       Business logic
  integrations/   Adapters for external systems
  middleware/     Request-ID/access logging middleware and exception handlers
  utils/          Small shared helpers
  main.py         Application factory (`create_app`) and ASGI `app`
```

## Frontend

```bash
cd frontend
npm ci
cp .env.example .env.local         # Windows: copy .env.example .env.local
npm run dev                        # http://localhost:5173
```

Other scripts: `npm test` (Vitest + React Testing Library), `npm run typecheck`, `npm run build`, `npm run preview`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `http://localhost:8000/api/v1` | Backend base URL including the version prefix. A relative path such as `/api/v1` also works behind a reverse proxy. |
| `VITE_API_TIMEOUT_MS` | `10000` | Per-request timeout |

The home page shows a live **Backend connection** panel that calls `/health` and `/ready`, which is the quickest way to confirm the two apps are connected.

### Frontend layout

```
frontend/src/
  app/         Entry composition: App, providers, routes, query client
  components/  Reusable UI (ErrorBoundary, StatusBadge)
  layouts/     AppShell (header, navigation, outlet)
  features/    Feature modules (system-status: API calls, query hooks, panel)
  pages/       Route-level pages
  hooks/       Shared hooks (API client injection, document title)
  services/    API client and environment config
  types/       API contract types
  utils/       Formatting and class-name helpers
  styles/      Tailwind entry and global styles
```

## Secrets

`.env` files are git-ignored at the root and in each app; only `.env.example` files are committed.
