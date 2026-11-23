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
| `DATABASE_URL` | empty | PostgreSQL URL, e.g. `postgresql+psycopg://user:pass@localhost:5432/opspilot`. Required for auth and all data endpoints |
| `JWT_SECRET_KEY` | empty | HMAC key for access tokens (32+ chars). Required in staging/production; random per start in development |
| `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_DAYS` | `15` / `14` | Access token lifetime / absolute session lifetime |
| `ALLOW_PUBLIC_REGISTRATION` | `true` | Allow self-service sign-up of new organizations |

The full list (cookie, Argon2 and rate-limit settings) is in `.env.example`.

### Database and migrations

```bash
cd backend
alembic upgrade head                      # create tables and seed roles/permissions
python -m app.cli create-platform-admin --email admin@example.com --name "Platform Admin"
                                          # password from OPSPILOT_ADMIN_PASSWORD or a prompt
```

Tests migrate a temporary SQLite database by default. To run them against PostgreSQL (the production
target), point `TEST_DATABASE_URL` at a disposable database; it is wiped at the start of the run:

```bash
TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/opspilot_test pytest
```

### Authentication and authorization

Endpoints under `/api/v1/auth`:

| Endpoint | Purpose |
| --- | --- |
| `POST /auth/register` | Create a new organization and its first user (Organization Admin), then sign in |
| `POST /auth/login` | Email + password. Returns a JWT access token and a refresh token (also set as an HttpOnly, SameSite=Strict cookie scoped to `/api/v1/auth`) |
| `POST /auth/refresh` | Rotate the refresh token (body or cookie) and get a new access token |
| `POST /auth/logout` | Revoke the current session (bearer token and/or refresh token). Always `204` |
| `POST /auth/logout-all` | Revoke every session of the current user |
| `GET /auth/me` | Current user, organization, roles and effective permissions |

Send the access token as `Authorization: Bearer <token>`. In the OpenAPI docs use **Authorize**.

How it works:

- **Passwords** are hashed with Argon2id; outdated hashes are upgraded on the next login. Policy: at least
  12 characters, three of four character classes, not a common password, not containing the email name.
- **Access tokens** are short-lived HS256 JWTs carrying only user, organization and session ids. Roles,
  permissions, account state and session state are re-checked on every request, so deactivation, logout
  and role changes take effect immediately.
- **Refresh tokens** are random opaque strings stored only as SHA-256 digests. Each one works once. Presenting
  a used token again is treated as theft: the whole session is revoked and an audit event is written.
- **Login errors** are always the generic `401 Invalid email or password.` (unknown email, wrong password and
  disabled accounts look the same). Login is rate limited per client IP and per account (`429` + `Retry-After`).
- **Authorization** is permission-based: routes declare
  `Depends(require_permissions(Permission.USERS_READ))`; role names appear only in `app/security/rbac.py`.
  Roles: Super Admin, Organization Admin, Operations Manager, Support Agent, Analyst, Developer, Viewer.
  Users can only assign roles whose permissions they hold themselves.
- **Tenant isolation**: the organization always comes from the authenticated identity. Repositories take
  the organization id explicitly; another tenant's records are `404`. A client-supplied `organization_id`
  is honoured only for holders of `platform.admin` (Super Admin) and is otherwise `403`.
- **Audit events** are written for registration, login success/failure, refresh, refresh-token reuse, logout,
  permission denials and user administration. `GET /api/v1/audit-events` (needs `audit.read`).

User administration (organization-scoped): `GET/POST /api/v1/users`, `GET /api/v1/users/{id}`,
`PUT /api/v1/users/{id}/roles`, `POST /api/v1/users/{id}/deactivate|activate`, `GET /api/v1/roles`.

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
  models/         ORM models (organizations, users, RBAC, sessions/refresh tokens, audit events)
  schemas/        Pydantic request/response contracts
  repositories/   Data access (tenant-scoped queries take the organization id explicitly)
  security/       Password hashing/policy, JWT and refresh tokens, RBAC catalog, tenancy, rate limiting
  services/       Business logic
  integrations/   Adapters for external systems
  middleware/     Request-ID/access logging middleware and exception handlers
  utils/          Small shared helpers
  cli.py          Admin commands (create-platform-admin)
  main.py         Application factory (`create_app`) and ASGI `app`
backend/migrations/  Alembic migrations (URL comes from DATABASE_URL)
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
