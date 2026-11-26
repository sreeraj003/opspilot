# AI providers and the LLM service layer (Module 08)

OpsPilot talks to a large language model through one service, `LLMService`. This guide covers the architecture,
NVIDIA configuration, every environment variable, how to add a provider, and how to test.

Module 08 is infrastructure only. It does not generate SQL, run SQL, use retrieval or embeddings, or provide a chat
interface.

## 1. Architecture

```
application module (later: SQL generation, answers)
        │  await llm_service.generate(messages=[...])
        ▼
LLMService  app/ai/services/llm_service.py
  • selects the active provider (AI_PROVIDER)        • validates the request (roles, counts, sizes, parameters)
  • applies defaults and ceilings                   • bounded retries for transient failures only
  • measures latency, normalises the response       • records usage (ai_usage_records) and structured logs
        │
        ▼
LLMProvider (abstract)  app/ai/providers/base.py      generate(...), health_check()
  ├── OpenAICompatibleProvider   app/ai/providers/openai_compatible.py   (HTTPX, POST /chat/completions, GET /models)
  │     └── NvidiaProvider       app/ai/providers/nvidia.py
  └── (future providers)
        │
        ▼
one shared httpx.AsyncClient per backend process (app/ai/providers/http.py, created in the FastAPI lifespan)
```

| Package | Contents |
| --- | --- |
| `app/ai/config/` | `ProviderConfigService`: builds one `ProviderConfig` per provider from the central `Settings` and picks the active one |
| `app/ai/exceptions/` | Typed errors: `ProviderConfigurationError`, `ProviderAuthenticationError`, `ProviderRateLimitError`, `ProviderTimeoutError`, `ProviderConnectionError`, `ProviderResponseError`, `ProviderUnavailableError` |
| `app/ai/schemas/` | `LLMMessage`, `LLMGenerationRequest`, `LLMGenerationResponse`, `ProviderCompletion`, `ProviderHealth`, admin views |
| `app/ai/providers/` | The interface, the OpenAI-compatible adapter, NVIDIA, the shared HTTP client, the registry |
| `app/ai/services/` | `LLMService`, `UsageService`, `ProviderAdminService` |
| `app/ai/dependencies/` | FastAPI providers, including `LLMServiceDep` for later modules |
| `app/models/ai_usage.py`, `app/repositories/ai_usage_repository.py` | Usage records |
| `app/api/routes/ai.py` | Administration API |

No vendor SDK is used. NVIDIA's API is OpenAI-compatible, so NVIDIA is the OpenAI-compatible adapter with NVIDIA's
name; both share the single HTTP client.

### Using the service from another module

```python
from app.ai.dependencies import LLMServiceDep
from app.ai.schemas import LLMMessage

async def explain(llm: LLMServiceDep, principal: CurrentPrincipalDep) -> str:
    response = await llm.generate(
        messages=[
            LLMMessage(role="system", content="You are a business intelligence assistant."),
            LLMMessage(role="user", content="Explain what total revenue means."),
        ],
        operation="explain_metric",          # stored with the usage record
        user_id=principal.user.id,
        metadata={"feature": "glossary"},    # simple values only, logged; never prompt text
    )
    return response.content
```

`generate` raises `LLMRequestValidationError` (422) for an invalid request and a `ProviderError` subclass when the
provider cannot answer. Each is an `AppError`, so an unhandled one becomes the standard error envelope with a fixed,
safe message. Before calling the provider, the service ends the request's implicit database read transaction, so no
pooled connection is held during a slow model call.

### Requests and responses

| `LLMGenerationRequest` | Rule |
| --- | --- |
| `messages` | 1 to 50 `LLMMessage`s; at least one user or assistant message |
| `LLMMessage.role` | `system`, `user` or `assistant` |
| `LLMMessage.content` | 1 to 32,000 characters, not blank, no NUL; 120,000 characters in total |
| `temperature` | 0 to 2; default `AI_TEMPERATURE` |
| `max_tokens` | 1 to 32,768, and never above `AI_MAX_OUTPUT_TOKENS` (lowered to it) |
| `model` | Optional override for this request (same provider) |
| `metadata` | Up to 20 `snake_case` keys with simple values; secret-looking keys are refused |

`LLMGenerationResponse` has `content`, `provider`, `model`, `finish_reason`, `input_tokens`, `output_tokens`,
`total_tokens`, `latency_ms`, `request_id` (`llm_…`) and `attempts`. Token counts are what the provider reported, or
`null`. `total_tokens` is the provider's figure; if the provider omits it but reports both input and output, it is
their exact sum. Nothing is estimated from text length.

### Errors, retries and timeouts

| Outcome | Error (HTTP if unhandled) | Retried? |
| --- | --- | --- |
| No key/model, unknown model or endpoint (404) | `ProviderConfigurationError` (503 `AI_PROVIDER_NOT_CONFIGURED`) | No |
| 401 / 403 | `ProviderAuthenticationError` (502) | Never |
| 429 | `ProviderRateLimitError` (503, with `Retry-After`) | Yes, honouring `Retry-After` |
| 502 / 503 | `ProviderUnavailableError` (503) | Yes |
| 500, other 5xx | `ProviderUnavailableError` | No (outcome ambiguous) |
| 504, read timeout after sending | `ProviderTimeoutError` (504) | No (outcome ambiguous) |
| Connect timeout, connection refused, DNS failure | `ProviderTimeoutError` / `ProviderConnectionError` | Yes (nothing was sent) |
| Connection dropped mid-response | `ProviderConnectionError` | No |
| 400 / 413 / 422 | `ProviderResponseError` (`invalid_request`) | No |
| Non-JSON, oversized (> 4 MB) or malformed body, redirect | `ProviderResponseError` | No |

Retries are bounded by `AI_MAX_RETRIES` (default 2, so at most 3 attempts). Waits use exponential backoff with jitter
(0.5 s, 1 s, 2 s …) capped at `AI_RETRY_MAX_WAIT_SECONDS`. A `Retry-After` longer than that cap fails at once instead
of waiting. Each attempt is limited by `AI_REQUEST_TIMEOUT_SECONDS`. There is no unbounded loop.

A provider's error body is never returned or logged, because it can echo the request. Only the HTTP status and a
sanitised error type (for example `invalid_api_key`) are logged.

### Usage records

Every `generate` call that reaches the provider writes one `ai_usage_records` row: provider, model, operation,
success or failure with its error category, finish reason, reported token counts (or NULL), latency, attempts,
OpsPilot's request id, the HTTP correlation id (`X-Request-ID`), the provider's own request id, the user, and the
time. No prompt, response, key or cost is stored. Prices are not calculated, because OpsPilot has no trustworthy price
list. Operational events, such as an administrator running the provider test (`ai_provider_tested`), go to the
existing audit trail (`auth_events`), not the usage table.

### Logging

Structured JSON lines on the `app.ai` logger, each with the HTTP `request_id` and the `llm_request_id`:
`ai.request.started` (message count, input characters, max output, temperature), `ai.request.retry`,
`ai.request.completed` (latency, attempts, finish reason, `usage_input`/`usage_output`/`usage_total`, output
characters), `ai.request.failed`, `ai.request.rate_limited`, `ai.request.retry_after_too_long`, `ai.provider.http_error`,
`ai.provider.health`, `ai.provider.selected` (debug), `ai.usage.store_failed`. Prompts, answers and API keys are never
logged. Message objects print as `LLMMessage(role='user', chars=34)`.

## 2. NVIDIA configuration

1. Create an API key at <https://build.nvidia.com> (it starts with `nvapi-`) and choose a model, for example
   `meta/llama-3.1-70b-instruct`. Use the id exactly as NVIDIA lists it.
2. In the repository's root `.env` (copied from `.env.example`):

   ```
   AI_PROVIDER=nvidia
   NVIDIA_API_KEY=nvapi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   NVIDIA_MODEL=meta/llama-3.1-70b-instruct
   NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
   ```

   No quotes, no spaces. Leave the other `AI_*` values at their defaults to start.
3. Restart the backend: `docker compose up -d backend`. The log shows `AI provider configured`.
4. Sign in as an administrator, open **Administration → AI provider**, check that the health shows **Reachable**
   and **Model listed: Yes**, then press **Run test**. A passed test shows the latency and, if NVIDIA reports it,
   the token usage.

If no key is set, the application starts normally; the page shows **Not configured** and which variables are missing.

The key is read from the environment only. It is never stored in the database, returned by any API, shown in the
browser or logged. To rotate it, change `.env` and restart the backend.

## 3. Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `AI_PROVIDER` | `nvidia` | Active provider: `nvidia` or `openai_compatible` |
| `NVIDIA_API_KEY` | empty | NVIDIA API key (secret) |
| `NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` | NVIDIA endpoint (https only) |
| `NVIDIA_MODEL` | empty | NVIDIA model id |
| `OPENAI_COMPATIBLE_API_KEY` | empty | Key for another OpenAI-compatible endpoint (secret) |
| `OPENAI_COMPATIBLE_BASE_URL` | empty | Its base URL up to `/v1`; https (http allowed outside production for a local server) |
| `OPENAI_COMPATIBLE_MODEL` | empty | Its model id |
| `AI_TEMPERATURE` | `0.2` | Default temperature (0 to 2) |
| `AI_MAX_OUTPUT_TOKENS` | `1024` | Default and ceiling for output tokens (1 to 32,768) |
| `AI_REQUEST_TIMEOUT_SECONDS` | `60` | Per-attempt timeout (1 to 600) |
| `AI_MAX_RETRIES` | `2` | Extra attempts for transient failures (0 to 5) |
| `AI_RETRY_MAX_WAIT_SECONDS` | `20` | Longest wait between attempts, including `Retry-After` (0 to 120) |
| `AI_TEST_RATE_LIMIT_PER_USER` | `10` | Provider test runs per user per 5 minutes |

The backend uses `HTTPS_PROXY` from its environment if a corporate proxy is required to reach the provider.

## 4. Administration API and permissions

| Endpoint | Permission | Purpose |
| --- | --- | --- |
| `GET /api/v1/ai/providers` | `ai.provider.read` | All providers with model, base URL, whether a key is set and which variable holds it |
| `GET /api/v1/ai/providers/active` | `ai.provider.read` | The provider `LLMService` uses |
| `GET /api/v1/ai/providers/health` | `ai.provider.read` | Lists the provider's models (no generation, no tokens); `healthy`, `unhealthy` or `not_configured`, latency, whether the model is listed |
| `POST /api/v1/ai/providers/test` | `ai.provider.test` | Sends the fixed prompt "Reply with OK." (max 32 tokens, temperature 0). No body is read. Returns passed/failed, latency, usage, a short reply preview. Rate-limited per user |
| `GET /api/v1/ai/usage` | `ai.usage.read` | Usage records (`offset`, `limit`, `provider`, `status`, `operation`, `since`) with totals of reported tokens |

The three permissions are Module 04 system permissions, granted to the Administrator role by migration `0006` and
delegable to custom roles. There is no endpoint that accepts prompt text, and none that changes the provider
configuration: that is done in `.env` on the server.

## 5. Adding a provider

Application code never changes; only the AI package does.

1. **Implement the interface.** If the provider speaks the OpenAI chat-completions protocol, subclass
   `OpenAICompatibleProvider` (as `NvidiaProvider` does). Otherwise subclass `LLMProvider` and implement:
   - `generate(messages, *, model, temperature, max_tokens, request_id) -> ProviderCompletion`: one attempt; map
     failures to the typed `ProviderError`s, setting `retryable=True` only when the request was certainly not
     processed, and `retry_after_seconds` when the provider says so; never put provider text, URLs or keys into
     messages or logs; leave token counts `None` when not reported.
   - `health_check() -> ProviderHealth`: cheap, no generation, never raises for provider failures.
   Use the shared `httpx.AsyncClient` passed to the constructor; do not create another client.
2. **Add its settings** to `app/core/config.py` (`SecretStr` for keys, blank-to-None, URL validation) and to
   `.env.example`, `docker-compose.yml` and this guide; add its name to the `AI_PROVIDER` literal.
3. **Add a builder** in `ProviderConfigService` (`app/ai/config/provider_config.py`) that fills `ProviderConfig`,
   including `credential_reference` and `missing_settings`.
4. **Register it** in `PROVIDER_REGISTRY` (`app/ai/providers/registry.py`).
5. **Test it** with `httpx.MockTransport`, as `tests/ai/test_providers_and_service.py` does.

If a future provider's credentials must be stored in the database instead of the environment, encrypt them with the
Module 07 `CredentialCipher` (AES-256-GCM, its own associated data) and expose only `has_credentials`, exactly as
connectors do. Never store a plaintext key.

## 6. Testing

Automated tests never need a real key and never reach the network: the provider is an `httpx.MockTransport`.

```bash
cd backend
pytest tests/ai                       # unit tests run without a database; API tests need TEST_DATABASE_URL
```

- `tests/ai/test_providers_and_service.py`: configuration, interface, registry, validation, request format,
  normalisation, usage that is never invented, every error mapping, retry policy (bounded, backoff, `Retry-After`, no
  retry for authentication or ambiguous timeouts), logs without secrets or prompts, health checks, provider
  replacement.
- `tests/ai/test_ai_api.py` (PostgreSQL): the administration API, credential masking, usage records and totals,
  audit events, rate limiting, authentication and per-permission authorization, running without configuration.
- `src/pages/AIProviderPage.test.tsx` (frontend): configuration display, health, test interaction, loading and
  failure states, usage display, permission-based visibility.

**Optional live test** against the real provider (spends a few tokens), from a development checkout with the key in
the root `.env`:

```bash
cd backend
OPSPILOT_LIVE_AI_TESTS=1 pytest -m live_ai -q
# PowerShell:  $env:OPSPILOT_LIVE_AI_TESTS="1"; pytest -m live_ai -q
```

It is skipped unless `OPSPILOT_LIVE_AI_TESTS=1` is set and the active provider is configured.

## 7. Known limitations

- Configuration is environment-only: changing provider, model or key needs a backend restart. There is no database
  storage of provider configurations, by design for this module.
- No streaming, tool calls or JSON mode yet. Only the first choice of a completion is used.
- Reasoning models that return their answer outside `message.content` (with `content` null) are reported as
  malformed responses.
- The health check proves reachability and lists models; some providers list models without checking the key, so
  only the test prompt proves the key works.
- The test-run rate limit is per backend process (like the login limits).
- No monetary cost is shown, because prices are not known reliably.
