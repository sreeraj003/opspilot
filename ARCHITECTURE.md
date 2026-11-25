# OpsPilot AI architecture

This document describes how OpsPilot AI is deployed and how its parts fit together. Sections marked
**planned** describe later modules; Module 01 implements only the foundation.

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

## 3. Application database responsibility

OpsPilot's PostgreSQL database stores **OpsPilot's own data**, never the company's business data:

- Module 01: connection management, health check and the Alembic migration baseline (no tables yet).
- **Planned:** users and roles, permissions, registered schema metadata, external API configuration,
  conversations and answers, and audit records of every generated and executed query.

Access goes through SQLAlchemy 2.x:

- One `Database` object per process owns the engine (`pool_pre_ping`, bounded pool, connect timeout) and the
  session factory. It is created at startup and disposed at shutdown.
- Routes that need persistence depend on `get_db_session`: one session per request, committed on success,
  rolled back on any error, always closed.
- Schema changes are made only through Alembic migrations, generated from `Base.metadata`. The database URL
  comes from settings, never from `alembic.ini`. The backend container runs `alembic upgrade head` before
  serving.

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
  services/     business logic and orchestration
  schemas/      Pydantic request/response contracts
  database/     Base and session management (models added by later modules)
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

Rules for every module: route handlers stay thin, logic lives in services, dependencies are injected through
`app/api/deps.py`, and no module imports another module's routes.

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
