# OpsPilot AI

OpsPilot AI is an AI-powered Business Intelligence Assistant. People in a company ask business questions in
natural language; OpsPilot turns each question into SQL, passes that SQL through an independent security and
authorization layer, runs approved queries through the company's own existing GET API, and answers in plain
language based on the real results.

Each company installs and runs its **own independent copy** of OpsPilot AI. It is not a multi-tenant SaaS
product, and it never connects directly to the company's business database.

> **Status:** Modules 01 to 11 are implemented: foundation, application database, authentication, roles and
> permissions, the frontend design system, the schema registry, the company GET API connector, the AI provider
> layer, question understanding, SQL generation and the SQL security guard. Running approved queries and the chat
> interface arrive in later modules. See [PROJECT_STATUS.md](PROJECT_STATUS.md).

## Architecture overview

```
Browser ──> frontend (React SPA) ──HTTP/JSON──> backend (FastAPI) ──> application database (PostgreSQL)
                                                     │
                                                     ├──> company GET SQL API (connector)
                                                     └──> LLM provider (NVIDIA or OpenAI-compatible)
```

- **frontend/**: React + TypeScript single-page app. Talks to the backend only through one API client.
- **backend/**: FastAPI service. Owns configuration, logging, error handling and OpsPilot's own database.
- **Application database**: PostgreSQL holding OpsPilot's own data (users, registered schemas, settings,
  conversations in later modules). It is *not* the company's business database.

Details are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Technology stack

| Area | Technologies |
| --- | --- |
| Backend | Python 3.12+, FastAPI, Pydantic v2 + pydantic-settings, SQLAlchemy 2.x, Alembic, psycopg 3, Uvicorn, argon2-cffi (Argon2id), PyJWT, email-validator, HTTPX (tests) |
| Frontend | React 19, TypeScript (strict), Vite, Tailwind CSS 4, React Router, TanStack Query, React Hook Form + Zod, Lucide icons |
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
- `SECRET_KEY`: signs access tokens. Required (32+ characters) when `APP_ENV=production`, recommended everywhere.
- `CONNECTOR_ENCRYPTION_KEY` and `CONNECTOR_ALLOWED_HOSTS`: needed before you can create a company API connector
  (see [Company API connector](#company-api-connector)).
- `NVIDIA_API_KEY` and `NVIDIA_MODEL`: needed for AI features (see [AI provider](#ai-provider)). Optional: without
  them the application runs and the AI provider shows as not configured.

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
| `SECRET_KEY` | empty | Signs access tokens, 32+ characters. Required in production; without it (development only) a temporary key is generated per start |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access-token lifetime (1–60) |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | A session ends after this many days without use (1–30) |
| `SESSION_ABSOLUTE_MAX_DAYS` | `30` | A session ends after this many days in any case |
| `AUTH_COOKIE_SECURE` | empty | Empty: `Secure` cookies except in `development`/`test`. `false` is refused in production |
| `AUTH_COOKIE_SAMESITE` / `AUTH_COOKIE_DOMAIN` | `strict` / empty | Refresh-cookie attributes (see [Authentication](#authentication)) |
| `AUTH_RECORD_CLIENT_METADATA` | `true` | Store IP address and browser on sessions (shown to administrators) |
| `FRONTEND_BASE_URL` | first `CORS_ORIGINS` entry | Base of password-reset links |
| `PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` | `30` | Reset-link lifetime |
| `PASSWORD_RESET_ADMIN_LINKS_ENABLED` | `true` | Let administrators create one-time reset links |
| `LOGIN_MAX_FAILED_ATTEMPTS` | `5` | Consecutive failures before a temporary lock |
| `LOGIN_LOCKOUT_BASE_SECONDS` / `LOGIN_LOCKOUT_MAX_SECONDS` | `60` / `900` | First lock duration (doubles per further failure) and its cap |
| `AUTH_RATE_LIMIT_WINDOW_SECONDS` | `300` | Window for the limits below |
| `AUTH_RATE_LIMIT_PER_IP` / `AUTH_RATE_LIMIT_PER_ACCOUNT` | `30` / `10` | Login, forgot-password and reset attempts per window, per client IP and per email |
| `AUTH_REFRESH_RATE_LIMIT_PER_IP` | `300` | Token refreshes per window per client IP |
| `AI_PROVIDER` | `nvidia` | Active LLM provider: `nvidia` or `openai_compatible` |
| `NVIDIA_API_KEY` / `NVIDIA_MODEL` | empty | NVIDIA key and model id (optional; AI features need them) |
| `NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` | NVIDIA endpoint (must be https) |
| `OPENAI_COMPATIBLE_API_KEY` / `_BASE_URL` / `_MODEL` | empty | Another OpenAI-compatible endpoint |
| `AI_TEMPERATURE` / `AI_MAX_OUTPUT_TOKENS` | `0.2` / `1024` | Generation defaults; requests may lower max tokens, never raise it |
| `AI_REQUEST_TIMEOUT_SECONDS` / `AI_MAX_RETRIES` / `AI_RETRY_MAX_WAIT_SECONDS` | `60` / `2` / `20` | Per-attempt timeout and bounded retries for transient failures |
| `AI_TEST_RATE_LIMIT_PER_USER` | `10` | Provider test runs per user per 5 minutes |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `opspilot` / — / `opspilot` | Docker Compose database credentials |
| `VITE_API_BASE_URL` | `http://localhost:8000` | Backend URL as seen from the browser (built into the frontend) |
| `VITE_API_TIMEOUT_MS` | `10000` | Frontend request timeout |
| `AUTHORIZATION_POLICY_MAX_DEPTH` / `AUTHORIZATION_POLICY_MAX_CONDITIONS` | `4` / `50` | Deepest AND/OR nesting and most conditions in one permission policy |
| `AUTHORIZATION_CACHE_SIZE` | `1024` | Users whose resolved permissions are cached per backend process (checked against a database revision on every request; `0` disables) |
| `SCHEMA_IMPORT_MAX_BYTES` | `5242880` | Largest schema import request in bytes (5 MiB); larger requests get `413` |
| `SCHEMA_IMPORT_MAX_DEPTH` | `10` | Deepest JSON nesting accepted in an import (at least 6) |
| `SCHEMA_MAX_TABLES` / `SCHEMA_MAX_COLUMNS_PER_TABLE` / `SCHEMA_MAX_RELATIONSHIPS` | `2000` / `1000` / `5000` | Size limits of one registered schema |
| `CONNECTOR_ENCRYPTION_KEY` | empty | Dedicated AES-256 key (32 bytes, base64url) encrypting connector credentials. Must differ from `SECRET_KEY`. Without it, connectors cannot be created or used |
| `CONNECTOR_ENCRYPTION_PREVIOUS_KEYS` | empty | Old keys, comma-separated, still accepted for decryption during [key rotation](docs/CONNECTORS.md#5-credential-encryption-and-key-rotation) |
| `CONNECTOR_ALLOWED_HOSTS` | empty | Hosts a connector may call (`host` or `*.domain`). Empty allows none |
| `CONNECTOR_ALLOWED_PRIVATE_NETWORKS` | empty | Private CIDR ranges a connector may reach (blocked by default). Link-local is never allowed |
| `CONNECTOR_ALLOW_HTTP` | `false` | Allow `http://` connector URLs. Development and test only; refused in production |
| `CONNECTOR_CA_BUNDLE` | empty | Extra CA certificates (PEM path) for a company API with an internal CA |
| `CONNECTOR_MAX_URL_LENGTH` | `8192` | Longest request URL. Longer requests are refused, never truncated |
| `CONNECTOR_MAX_RESPONSE_BYTES_LIMIT` / `CONNECTOR_MAX_TIMEOUT_SECONDS` | `52428800` / `120` | Ceilings for per-connector response size and timeouts |
| `CONNECTOR_POOL_MAX_CONNECTIONS` / `CONNECTOR_POOL_MAX_KEEPALIVE` | `20` / `10` | Outbound connection pool per backend process |

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

On a new installation, create the first administrator next (below); nobody can sign in before that.

## First administrator

OpsPilot ships **no default account and no default password**, and there is no web page or API endpoint that
creates an administrator. The installation's first administrator is created once, from the server, with a command
that runs inside the backend container. It works the same in PowerShell, Command Prompt and bash.

With the stack running (`docker compose up -d`):

```bash
docker compose exec backend python -m app.cli create-admin
```

It asks for the administrator's email, full name and password (typed twice, hidden). The password must have at
least 12 characters; long passphrases are encouraged. Then open <http://localhost:5173> and sign in with that email
and password.

- It runs **once**. When an administrator already exists it refuses and changes nothing.
- Credentials are never printed or logged, and are never accepted as command-line arguments.
- Non-interactive alternative (automation): pass the values as environment variables of that one command:

  ```powershell
  docker compose exec -e OPSPILOT_ADMIN_EMAIL=owner@company.com -e OPSPILOT_ADMIN_FULL_NAME="Jane Owner" `
    -e OPSPILOT_ADMIN_PASSWORD="<a long passphrase>" backend python -m app.cli create-admin --no-input
  ```

  (bash: replace the backtick with `\`). Values given this way can end up in shell history; prefer the prompt.
- Without Docker: from `backend/` with `DATABASE_URL` set and migrations applied, run `python -m app.cli create-admin`.

**Lost administrator password?** Anyone with shell access to the server can set a new one; this also signs out every
session of that account:

```bash
docker compose exec backend python -m app.cli reset-password --email owner@company.com
```

## Authentication

Every page and every API endpoint except sign-in, password reset and the health checks requires a signed-in user.

- **Sign in** at `/login` with email and password. A session lasts 7 days without use (30 days at most, then sign
  in again). **Sign out** ends the session in this browser; **Account → Sign out everywhere** ends all of them.
- **Change your password** under **Account**. It requires the current password and signs out every other session.
- **Forgot password:** `/forgot-password` always answers the same way, whether or not the account exists. OpsPilot
  does **not send email yet**, so a self-service request only records an audit event; the user should ask an
  administrator for a reset link (below).
- **Tokens:** a 15-minute access token (kept only in the page's memory) and a refresh token in an `HttpOnly`
  cookie, rotated on every use. Nothing is stored in `localStorage`.
- **Cookies and HTTPS:** production must be served over **HTTPS**; the refresh cookie is then `Secure` and the
  backend refuses `AUTH_COOKIE_SECURE=false` when `APP_ENV=production`. On plain `http://localhost` (the default
  Docker setup, `APP_ENV=development`) the cookie is not marked `Secure` so it works without certificates. The
  browser app and the API must be on the **same site** (for example `localhost:5173` and `localhost:8000`, or
  `bi.company.com` and `api.bi.company.com`) because the cookie is `SameSite=Strict`, and the app's origin must be
  in `CORS_ORIGINS`.
- **Brute-force protection:** after 5 consecutive wrong passwords an account is locked for 1 minute, doubling per
  further failure up to 15 minutes; locks expire on their own. Sign-in attempts are also rate-limited per IP address
  and per email (HTTP 429). Error messages never reveal whether an account exists.

Design and trade-offs: [ARCHITECTURE.md §4](ARCHITECTURE.md#4-authentication-and-user-management-module-03).

## User management

Administrators see **Users** in the top navigation:

- **List and search** users by name, email or username; filter by status (active, inactive, locked); sort; page
  through results.
- **Create user:** full name, email, optional username and an **initial password** that you choose and share with
  the person over a trusted channel (there is no email invitation yet). They can change it under **Account**.
- **User page:** edit name, email and username; see last sign-in and active sessions; and, each after a
  confirmation: **Deactivate** (signs the user out everywhere at once and blocks sign-in; history is kept),
  **Activate** (also clears a temporary lockout), **Sign out all sessions**, **Create password reset link**
  (a one-time link, valid 30 minutes, shown once: copy it to the user; their sessions end when they set the new
  password) and **Delete** (soft delete: the account disappears from the list and cannot sign in; audit history is
  kept and the email can be reused).
- The installation administrator account cannot be deactivated or deleted from the web app.
- With `access:review`, the user page also shows the person's **roles**, **direct permissions** and **effective
  access**; with `access:assign` you can assign or remove roles and grant or deny individual permissions, each with an
  optional expiry date (see below).

## Roles and permissions

Access is configured in the web app, never in code. Nothing is hard-coded about job titles: you decide what a
"Regional Manager" or "Finance Analyst" may see.

1. **Create permissions** (**Permissions → Create permission**). Give it a name and a key such as
   `sales.karnataka.view`, choose the resource (`database_data` for tables, columns and rows; any other name, such as
   `executive_reports`, for features) and the action (`view`, `export`, …). Then build the rule with the form:
   - *Allow*: the tables, the columns of each (or all columns), whether individual rows and/or only aggregates may be
     read, **row restrictions** (for example *region equals Karnataka*; add a group to allow any of several values),
     and whether results may be exported or the generated SQL shown.
   - *Deny*: a restriction that always wins over allows: deny the action entirely, or deny tables, columns
     (`table.column`), matching rows, exporting or SQL viewing.

   **Review** checks the rule on the server, shows it in plain language and as read-only JSON, and only then saves.
   Policy reference: [docs/PERMISSION_POLICY.md](docs/PERMISSION_POLICY.md).
2. **Create roles** (**Roles → Create role**) and open a role to **Edit permissions**: tick the permissions, review the
   additions and removals, and save. Users holding the role get the change on their next request.
3. **Assign roles to users** on each user's page, optionally until a date. Use a direct **Deny** to take one
   permission away from one person even though a role grants it.

Rules worth knowing:

- Without a matching permission, everything is denied. A deny always wins over an allow. Expired or removed
  assignments and inactive roles or permissions stop counting immediately.
- The built-in **Administrator** role (marked *System*) manages users, roles, permissions and access. It cannot be
  edited or deleted, and its management permissions cannot be given to any other role. Read-only administration
  (`users:read`, `roles:read`, `permissions:read`, `settings:read`, `access:review`) can be delegated through any role.
- Administrators do **not** automatically see business data; give them a data permission like anyone else.
- Nobody (except the installation administrator) can change their own access, and the last administrator cannot be
  removed.
- Every change is recorded in an audit trail, visible as **History** on role and permission pages.
- If the built-in permissions or Administrator role were changed directly in the database, restore them with:

  ```bash
  docker compose exec backend python -m app.cli sync-permissions
  ```

Design details: [ARCHITECTURE.md §5](ARCHITECTURE.md#5-roles-and-permissions-module-04).

## Schema registry

OpsPilot needs to know what your business databases look like, without ever connecting to them. Under
**Administration → Schemas** you register each database's metadata:

1. **Register schema**: a name, the database type (PostgreSQL, MySQL, MariaDB, SQL Server, Oracle, SQLite or other)
   and an optional dialect and description.
2. **Add the metadata**, either by hand on the *Tables* tab (tables, then columns with their data type, primary key,
   foreign key, nullable and *sensitive* flags, plus a description and business meaning) and the *Relationships* tab,
   or by importing a JSON file on the *Import / Export* tab. The import is always validated and previewed first, and
   you choose how it treats what is already registered (*add new items only*, *merge* or *replace*). Format and
   examples: [docs/SCHEMA_IMPORT_FORMAT.md](docs/SCHEMA_IMPORT_FORMAT.md).
3. **Check it** on the *Overview* tab (counts and metadata checks such as tables without a primary key) and follow every
   change, with its version number, on *Schema History*.

Mark columns holding personal or secret data as **sensitive**: they are left out of the AI's schema context unless a
later module explicitly asks for them. Inactive tables, columns and relationships are kept but ignored.

Access is granted with seven permissions you can put in any role: `schema.read`, `schema.create`, `schema.update`,
`schema.delete`, `schema.import`, `schema.export` and `schema.relationship.manage`. The Administrator role has all
of them. Once tables are registered, data permissions (`database_data` policies) are checked against them, so a policy
that names a table or column that does not exist is refused.

Design details: [ARCHITECTURE.md §7](ARCHITECTURE.md#7-external-schema-registry-module-06).

### Schema registry API

All under `/api/v1`, authenticated, with the standard error format. Lists take `offset`, `limit` (≤ 100) and
`search`; most take `status` (`all`, `active`, `inactive`). Full request and response schemas are in the OpenAPI
documentation at `/docs`.

| Endpoint | Permission | Purpose |
| --- | --- | --- |
| `POST /schemas`, `GET /schemas` | `schema.create`, `schema.read` | Register; list (filters `database_type`, `status`; `sort` = `name`, `updated_at`, `created_at`, `schema_version`; `order`) |
| `GET`, `PATCH`, `DELETE /schemas/{id}` | read, update, delete | One source; edit name, description, type, dialect, active flag or glossary; delete with everything in it |
| `POST`, `GET /schemas/{id}/tables` | update, read | Add a table (optionally with columns); list (filter `schema_name`) |
| `GET`, `PATCH`, `DELETE /tables/{id}` | read, update, delete | A table with its columns; edit; delete (`?cascade_relationships=true` to delete the relationships using it) |
| `POST`, `GET /tables/{id}/columns` | update, read | Add a column; list |
| `PATCH`, `DELETE /columns/{id}` | update, delete | Edit; delete (`?cascade_relationships=true`) |
| `POST`, `GET /schemas/{id}/relationships` | `schema.relationship.manage`, read | Add; list (filter `table_id`) |
| `PATCH`, `DELETE /relationships/{id}` | `schema.relationship.manage` | Edit; delete |
| `POST /schemas/{id}/import` | `schema.import` | JSON import with `strategy`, `dry_run`, `confirm_replace`, `expected_schema_version` ([format](docs/SCHEMA_IMPORT_FORMAT.md)) |
| `GET /schemas/{id}/export` | `schema.export` | The metadata in the import format |
| `GET /schemas/{id}/summary` | read | Counts and current validation issues |
| `GET /schemas/{id}/version` | read | Current version and the change that produced it |
| `GET /schemas/{id}/history` | read | Change history, newest first |
| `GET /schemas/{id}/outline` | read | Every table and column in one response (for pickers) |
| `GET /schemas/{id}/representation` | read | AI-ready structured metadata (filters `schema_name`, `table`, `include_related`, `include_sensitive`, `include_descriptions`, `include_business_terms`) |

Errors specific to the registry: `422 SCHEMA_VALIDATION_FAILED` (details: the list of issues with code, message,
location and severity), `409 IN_USE` (a relationship uses the table or column), `409 SCHEMA_VERSION_CONFLICT` (the
schema changed since the preview), `409 CONFLICT` (duplicate name), `413 PAYLOAD_TOO_LARGE` and `422 JSON_TOO_DEEP`.


## Company API connector

OpsPilot never connects to your business database. Instead your company exposes one HTTPS **GET** endpoint that runs
a SQL statement passed as a query parameter and returns JSON, for example
`GET https://company-backend.example.com/api/sql/execute?sql=SELECT%20...`. A **connector** tells OpsPilot how to call
it.

1. In `.env`, set:
   - `CONNECTOR_ENCRYPTION_KEY`, generated with
     `python -c "import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`;
   - `CONNECTOR_ALLOWED_HOSTS`, the company API's host name.

   Then restart the backend with `docker compose up -d backend`.
2. Under **Administration → Connectors**, choose **New connector**. Enter the name, the schema it serves, the base URL,
   the endpoint path, the SQL parameter name, the authentication (none, API key, bearer token or a custom header), any
   extra headers, and the response format.
3. Open the connector, **Test connection** (a fixed `SELECT 1` or your health endpoint, never arbitrary SQL), then
   **Activate** it. Only one connector per schema can be active.

Credentials and secret headers are encrypted with AES-256-GCM. They are never shown again, returned by the API or
logged. To replace one, type a new value; leave the field empty to keep it. Destinations are checked against the
allowlist and against private, loopback and link-local addresses on every request, redirects are never followed, and
TLS is always verified. SQL travels in the URL, so configure your company API's web server and proxies not to log the
query string.

Full guide, with a worked example, the response contract, key rotation, SSRF deployment requirements and the risks of
SQL in GET URLs: [docs/CONNECTORS.md](docs/CONNECTORS.md). Design: [ARCHITECTURE.md §8](ARCHITECTURE.md#8-company-get-api-connector-module-07).

### Connector API

All under `/api/v1`, authenticated, with the standard error format. Responses never contain credentials: only
`has_credentials`, a fixed `credential_mask`, and `has_value` for secret headers.

| Endpoint | Permission | Purpose |
| --- | --- | --- |
| `POST /connectors` | `connector.create` | Create (always inactive) |
| `GET /connectors` | `connector.read` | List (`offset`, `limit`, `search`, `status`, `authentication_type`, `last_test_status`, `schema_source_id`, `sort` = `name`, `updated_at`, `created_at`, `last_tested_at`, `order`) |
| `GET /connectors/environment` | `connector.read` | Whether encryption is configured, the approved hosts and the server limits |
| `GET /connectors/{id}` | `connector.read` | One connector, masked, with any current destination problem |
| `PATCH /connectors/{id}` | `connector.update` | Edit; an omitted credential or secret header value keeps the stored one |
| `DELETE /connectors/{id}` | `connector.delete` | Delete and erase stored ciphertext |
| `PATCH /connectors/{id}/status` | `connector.activate` | `{"is_active": true/false}` |
| `POST /connectors/{id}/test` | `connector.test` | Run the connection test (no request body; no SQL accepted) |
| `GET /connectors/{id}/health` | `connector.read` | Health from the configuration checks (destination, credentials, schema) and the last test, without calling the company API |
| `GET /connectors/{id}/audit` | `connector.read` | Audit events (never credentials or SQL text) |

There is deliberately no endpoint that accepts SQL. Errors specific to connectors include
`422 CONNECTOR_DESTINATION_REJECTED`, `422 CONNECTOR_CONFIGURATION_INVALID`, `503 CONNECTOR_ENCRYPTION_NOT_CONFIGURED`,
`503 CONNECTOR_CREDENTIALS_UNAVAILABLE` and `409 CONFLICT` (duplicate name or endpoint, or another active connector
for the schema).

## AI provider

OpsPilot reaches its language model through one backend service (`LLMService`). NVIDIA's API is used by default;
any OpenAI-compatible endpoint can be selected instead with `AI_PROVIDER=openai_compatible`.

1. In `.env`, set `NVIDIA_API_KEY` (from <https://build.nvidia.com>, starts with `nvapi-`) and `NVIDIA_MODEL` (for
   example `meta/llama-3.1-70b-instruct`), then restart the backend with `docker compose up -d backend`.
2. Under **Administration → AI provider**, check the health and press **Run test**. The test sends a fixed prompt
   chosen by the server; there is no way to send your own text from this page.

The key stays in the server environment: it is never stored in the database, returned by the API, shown in the
browser or logged. Every AI request is recorded with the token counts the provider reported (never estimated, no
costs).

| Endpoint (under `/api/v1`) | Permission | Purpose |
| --- | --- | --- |
| `GET /ai/providers`, `GET /ai/providers/active` | `ai.provider.read` | Provider, model, endpoint, whether a key is set |
| `GET /ai/providers/health` | `ai.provider.read` | Reachability and whether the model is listed (no tokens used) |
| `POST /ai/providers/test` | `ai.provider.test` | Fixed test prompt; latency and reported usage |
| `GET /ai/usage` | `ai.usage.read` | Usage records and totals |

Architecture, NVIDIA setup, every variable, error and retry rules, how to add a provider and how to test:
[docs/AI_PROVIDERS.md](docs/AI_PROVIDERS.md).

## Question understanding

OpsPilot turns a business question into a validated, structured **business intent**: metrics, dimensions, filters,
resolved dates, sorting, assumptions and, when something is unclear, one clarification question. It uses only
registered schemas, registered business terms and the asking user's own data permissions. It does not generate or
run SQL and is not a chatbot.

1. Under **Administration → Business terms**, define what words such as "Revenue" or "Active customer" mean: a
   description, synonyms, the registered tables and columns that define them and the default aggregation. The AI
   never invents a definition; an undefined or ambiguous term leads to a clarification question.
2. Under **Administration → Question preview**, pick a schema, enter a question and inspect the interpretation, its
   validation and permission status. Administrators with `settings:manage` set the business timezone there (default
   `UTC`, or `BUSINESS_TIMEZONE` in `.env`), used to resolve "yesterday", "last month" and similar.

| Endpoint (under `/api/v1`) | Permission | Purpose |
| --- | --- | --- |
| `POST /ai/understand` | `ai.question.understand` | Interpret a question |
| `POST /ai/understand/preview` | `ai.question.preview` | The same with diagnostics |
| `GET /ai/understand/options` | either | Schemas and business timezone |
| `GET`/`POST /business-terms`, `GET`/`PATCH`/`DELETE /business-terms/{id}` | `business_term.read` / `business_term.manage` | Business terms |

Contract, intent types, time rules, clarification rules, model output contract and samples:
[docs/QUESTION_UNDERSTANDING.md](docs/QUESTION_UNDERSTANDING.md).

## SQL generation

OpsPilot turns an interpreted question into **candidate SQL** for the schema's database (PostgreSQL, MySQL or SQL
Server). Generated SQL is **untrusted**: it is never executed by this step, never sent to the company API and must
pass the SQL security guard (below) first.

Under **Administration → SQL generation**, pick a schema and ask a question. OpsPilot interprets it, generates SQL
from the stored interpretation using only the registered tables and columns you may see, validates it with SQLGlot
and shows it as "Generated — Awaiting Security Validation", with tables, columns, joins and assumptions. The SQL
text is shown only when your data permission allows viewing SQL (`allow_sql_view`).

| Endpoint (under `/api/v1`) | Permission | Purpose |
| --- | --- | --- |
| `POST /ai/sql/generate` | `ai.sql.generate` | Generate SQL for a stored intent (`intent_id`, `schema_source_id`) |
| `GET /ai/sql/generations/{id}` | `ai.sql.generate` | One of your generations |
| `POST /ai/sql/generations/{id}/regenerate` | `ai.sql.generate` | Generate again (bounded) |

Contract, dialects, validation rules, limitations and configuration: [docs/SQL_GENERATION.md](docs/SQL_GENERATION.md).

## SQL security guard

Every generated query is checked again by a deterministic guard (no AI) before it can ever reach the company API.
In the SQL generation result, **Validate SQL** (permission `ai.sql.validate`) re-parses the stored query with SQLGlot,
allows exactly one read-only SELECT, checks every table, column, join and function against the registry and an
allowlist, re-checks your current permissions, applies your row restrictions to every part of the query, enforces
complexity and row limits, and either rejects it with a safe reason or approves the **exact** rewritten SQL. An
approval is single use, bound to you, the connector, the schema version and your permissions, and expires after
five minutes. Nothing executes yet: there is no execute button or endpoint.

| Endpoint (under `/api/v1`) | Permission | Purpose |
| --- | --- | --- |
| `POST /ai/sql/generations/{id}/validate` | `ai.sql.validate` | Validate one of your generations |
| `GET /ai/sql/generations/{id}/validation` | `ai.sql.validate` | Its latest validation |
| `GET /ai/sql/validations/{id}` | `ai.sql.validate` or `ai.sql.security.audit` | One validation (auditors also see internal reasons) |
| `GET /ai/sql/approvals/{id}` | `ai.sql.validate` | Approval status and expiry |
| `POST /ai/sql/approvals/{id}/revoke` | `ai.sql.validate` | Revoke an unused approval |

Rules, row-level security, limits, approval lifecycle and limitations: [docs/SQL_SECURITY.md](docs/SQL_SECURITY.md).

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

Other scripts: `npm run build`, `npm run preview`, `npm run typecheck`, `npm run lint`.

The interface is built from a shared design system (tokens, light and dark themes, components, layouts and a
navigation registry): see [docs/FRONTEND_DESIGN_SYSTEM.md](docs/FRONTEND_DESIGN_SYSTEM.md).

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

AI provider tests mock the provider and never need a key. An optional live test against the real provider is
skipped unless you opt in: `OPSPILOT_LIVE_AI_TESTS=1 pytest -m live_ai` (see [docs/AI_PROVIDERS.md](docs/AI_PROVIDERS.md#6-testing)).

Safety: pytest stops if the test database's name does not contain `test`, or if it is the same database as
`DATABASE_URL`. Each run rebuilds the test schema with the migrations; the migration test creates and drops its own
scratch database (the test user needs `CREATEDB`, which the test container's user has). Stop the container with
`docker compose -f docker-compose.test.yml down` (its data is discarded).

### Frontend

```bash
cd frontend
npm test                           # Vitest + React Testing Library
npm run typecheck
npm run lint                       # ESLint (TypeScript, React hooks, jsx-a11y)
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

Authentication failures are `401 UNAUTHORIZED` (or `INVALID_CREDENTIALS` for a failed sign-in) with a
`WWW-Authenticate: Bearer` header, missing permissions are `403 FORBIDDEN`, and rate limits are
`429 TOO_MANY_REQUESTS` with `Retry-After`. Unexpected exceptions return `500 INTERNAL_SERVER_ERROR` with a generic message; the traceback goes to the logs
only. Logs are JSON lines with `timestamp`, `level`, `module`, `message` and `request_id`. Fields that look like
secrets (`password`, `token`, `api_key`, `authorization`, `database_url`, …) are redacted.

## Project layout

```
backend/
  app/
    api/            deps.py (dependency providers, auth + permission dependencies), router.py, presenters.py,
                    routes/ (thin HTTP handlers: system, health, auth, users, settings)
    cli/            python -m app.cli (create-admin, reset-password)
    schema_registry/ Pure schema-registry logic: validation, import planning, AI representation, data types, JSON limits
    core/           config.py, logging.py, exceptions.py, middleware.py, security.py (tokens),
                    authorization.py (permissions, policy), rate_limit.py, emails.py
    database/       base.py (Base), naming.py, session.py (engine, sessions), transaction.py, health.py
    models/         SQLAlchemy models (mixins, application_setting, user, user_session, password_reset_token, auth_event,
                    rbac, schema_registry)
    repositories/   Data access (base, application settings, users, sessions, reset tokens)
    schemas/        Pydantic request/response contracts
    services/       Business logic and transaction boundaries (health, settings, auth, users, passwords, audit, bootstrap)
    main.py         Application factory and ASGI app
  migrations/       Alembic environment and versions/ (0001: application_settings, 0002: authentication tables,
                    0003: roles and permissions, 0004: schema registry)
  tests/            API and config tests; tests/database/ database layer; tests/auth/ authentication and users
frontend/
  src/
    app/            App, providers, routes, navigation registry, breadcrumbs (route handles), lazy pages
    components/     ui/ design-system components; status cards, error boundary, brand mark
    design/         tokens.ts (breakpoints, z-index, durations mirrored from styles/tokens.css)
    features/       auth/, permissions/, rbac/, schemas/ (schema registry tabs, forms, validation), users/,
                    theme/ (provider, toggle), product/
    layouts/        PublicLayout, AuthLayout, AppLayout, AdminLayout; shell/ (sidebar, top bar, menus)
    pages/          Home, Login, ForgotPassword, ResetPassword, Account, Users, UserDetail, Roles, Permissions,
                    Schemas, SchemaDetail, NotFound
    services/       API client, auth session (in-memory token, refresh), typed auth/user/system calls
    hooks/          API client injection, status queries, document title
    types/          API contract types
    utils/, styles/ (index.css, tokens.css)
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
| Nobody can sign in on a new installation | Create the first administrator: `docker compose exec backend python -m app.cli create-admin`. |
| Signed out after every page reload | The refresh cookie is not stored: the app and API must be on the same site, the app's origin must be in `CORS_ORIGINS`, and over plain `http://` (other than localhost) `AUTH_COOKIE_SECURE` must not be forced to `true`. Production needs HTTPS. |
| Sign-in answers "Too many attempts" | Rate limit reached; wait a few minutes. Behind a reverse proxy, run uvicorn with `--forwarded-allow-ips` so users are not counted as one IP. |
| An account stays locked | Locks expire on their own (15 minutes at most); an administrator can clear one with **Activate** on the user's page. |
| Administrator password lost | `docker compose exec backend python -m app.cli reset-password --email <admin email>`. |
| `Psycopg cannot use the 'ProactorEventLoop'` (Windows, no Docker) | Start uvicorn with `--reload`, or add `--loop asyncio:SelectorEventLoop`. |
| `pytest` exits with `TEST_DATABASE_URL must ...` | Point `TEST_DATABASE_URL` at a separate database whose name contains `test` (see Database tests). |
| Frontend shows **Unreachable** | The backend is down, `VITE_API_BASE_URL` is wrong, or the page origin is missing from `CORS_ORIGINS`. |
| Changed `VITE_API_BASE_URL` has no effect in Docker | It is built into the bundle: run `docker compose up --build frontend`. |
| Port 8000, 5173 or 5432 is busy | Set `BACKEND_PORT`, `FRONTEND_PORT` or `POSTGRES_PORT` in `.env` (update `CORS_ORIGINS` to match). |
| `pip`/`npm` fail with certificate errors during `docker compose build` | A TLS-intercepting corporate proxy is in the way. Add its CA to the build environment; do not disable verification. |

## Secrets

`.env` files are git-ignored; only `.env.example` is committed. Docker images contain no secrets: they receive
configuration at runtime. `VITE_*` values are compiled into browser JavaScript, so they must never hold secrets.
