# Company API connector (Module 07)

OpsPilot never connects to your business database and never stores database
credentials. Your company exposes **one HTTPS GET endpoint** that receives a SQL
statement as a query-string parameter, runs it against your database with a
read-only account, and returns JSON. An OpsPilot **connector** stores how to
call that endpoint: its URL, how to authenticate, and the shape of its JSON.

This guide covers:

1. [How a request is built](#1-how-a-request-is-built)
2. [Example: configuring Acme's endpoint](#2-example-configuring-acmes-endpoint)
3. [Response contract](#3-response-contract)
4. [Connection test](#4-connection-test)
5. [Credential encryption and key rotation](#5-credential-encryption-and-key-rotation)
6. [Network and SSRF protection: deployment requirements](#6-network-and-ssrf-protection-deployment-requirements)
7. [Security considerations of SQL in a GET query string](#7-security-considerations-of-sql-in-a-get-query-string)
8. [Error categories](#8-error-categories)
9. [Trusted execution boundary](#9-trusted-execution-boundary)
10. [Permissions](#10-permissions)

## 1. How a request is built

```
GET {base_url}{endpoint_path}?{extra params}&{sql_parameter_name}={url-encoded SQL}
Host: {configured host}
Accept: application/json
{authentication header}
{custom headers}
```

- The method is always GET. OpsPilot never switches to POST, even for long
  queries.
- Every parameter is percent-encoded (spaces become `%20`, and `&`, `=`, `#`, `+`,
  quotes and non-ASCII characters are all escaped), so SQL cannot break out of
  its parameter.
- SQL is never truncated. If the SQL exceeds the connector's **Max SQL length**,
  or the finished URL exceeds `CONNECTOR_MAX_URL_LENGTH`, the request is refused
  before anything is sent.
- Redirects are never followed. A 3xx response is an error.
- Proxy environment variables (`HTTP_PROXY`, `HTTPS_PROXY`) are ignored, and
  cookies are never stored or sent.
- TLS certificates are always verified (TLS 1.2 or newer). Add an internal CA
  with `CONNECTOR_CA_BUNDLE`. Verification cannot be turned off.

Authentication options:

| Type | Header sent |
| --- | --- |
| None | none |
| API key | `X-API-Key: <key>` (header name configurable) |
| Bearer token | `Authorization: Bearer <token>` |
| Custom header | `<your header name>: <value>` |

Custom headers (up to 20) can be plain, such as `X-Tenant: acme`, or **secret**,
such as a signing key. Secret headers are encrypted like the credential.
Headers that HTTP or OpsPilot controls (`Host`, `Content-Length`, `Cookie`,
`Authorization` and others) are refused.

## 2. Example: configuring Acme's endpoint

Acme's backend team runs a small service at
`https://company-backend.acme.example` with one endpoint:

```
GET /api/sql/execute?format=json&sql=<SQL>
X-API-Key: <key issued to OpsPilot>
X-Tenant: acme
```

On their side, the service:

- connects to the warehouse with a **read-only** database user that can see only
  the tables listed in OpsPilot's schema registry;
- enforces a statement timeout (for example 25 s) and a row limit;
- rejects anything that is not a single `SELECT`;
- logs requests **without** the `sql` query parameter (see section 7);
- answers in the reference contract shown in section 3.

**Server configuration** (`.env`):

```dotenv
CONNECTOR_ENCRYPTION_KEY=<generated, see section 5>
CONNECTOR_ALLOWED_HOSTS=company-backend.acme.example
# Only if company-backend.acme.example resolves to an internal address:
CONNECTOR_ALLOWED_PRIVATE_NETWORKS=10.20.0.0/16
```

Restart the backend (`docker compose up -d backend`).

**In OpsPilot** (Administration › Connectors › New connector):

| Field | Value |
| --- | --- |
| Connector name | Acme warehouse API |
| Associated schema | The schema imported in Module 06, for example "Sales DB" |
| Base URL | `https://company-backend.acme.example` |
| Endpoint path | `/api/sql/execute` |
| SQL parameter name | `sql` |
| Extra query parameters | `format` = `json` |
| Authentication | API key, header `X-API-Key`, key `<the key>` |
| Custom headers | `X-Tenant` = `acme` (not secret) |
| Health check path | `/api/health` (optional) |
| Response format | Envelope (`success`, `data`, `metadata`, `error`) |
| Timeouts | connect 5 s, read 30 s, overall 30 s |
| Max response size | 5 MB |
| Max SQL length | 4000 characters |

Then open the connector, click **Test connection**, and click **Activate** once
the test passes. A new connector is always created inactive, and only one
connector per schema can be active.

The request OpsPilot sends for the connection test (with no health path) is:

```
GET https://company-backend.acme.example/api/sql/execute?format=json&sql=SELECT%201%20AS%20opspilot_connection_test
```

**Editing.** Saved credentials are never shown again, not even partly. Leave a
credential or secret header field empty to keep the stored value, or type a new
value to replace it. Changing the URL, path, parameter, authentication or
timeouts clears the last test result, so test again.

## 3. Response contract

The adapter never guesses field names. The expected shape is part of each
connector's configuration.

### Envelope (default, the reference contract)

Success:

```json
{
  "success": true,
  "data": [{ "region": "EMEA", "total_orders": 1250 }],
  "metadata": { "execution_time_ms": 45 }
}
```

Failure:

```json
{ "success": false, "error": { "code": "QUERY_TIMEOUT", "message": "…" } }
```

- The field names `success`, `data`, `metadata` and `error` can each be renamed per
  connector.
- `success` must be a JSON `true` or `false`. A missing or non-boolean value is
  a malformed response.
- `success: false` is always an error, whatever else the body contains. Only a
  short `error.code` (letters, digits, `_ . -`) is kept. The company API's
  error message is not shown or stored, because it may echo SQL or data.
- `data` must be an array of objects. `[]` is a successful, empty result, which
  is different from every error.
- `metadata.execution_time_ms` is optional.

### Raw array

The body is the array of row objects itself: `[{"total_orders": 1250}]`.

### Rules for both formats

- The response must have status 2xx, a JSON content type
  (`application/json` or `application/*+json`) and valid UTF-8 JSON. `NaN`
  and `Infinity` are rejected.
- The body may not exceed the connector's maximum response size. The limit is
  checked against `Content-Length` and again while streaming.
- Column names and values are kept exactly as received. Missing keys are not
  filled in, and values are not converted or invented.
- 401 and 403 are reported as authentication failures, and any other non-2xx
  status as an HTTP error.

## 4. Connection test

`POST /api/v1/connectors/{id}/test` takes **no body**, so it cannot be used to run
SQL. It runs this sequence:

1. Configuration and credentials can be decrypted.
2. The destination passes the SSRF policy (static check and DNS).
3. The request:
   - If a **health check path** is configured, a GET is sent to it with the
     same authentication. Any JSON document passes, unless it is an envelope
     with `success: false`.
   - Otherwise the fixed statement `SELECT 1 AS opspilot_connection_test` is sent
     through the normal endpoint, and the response must satisfy the response
     contract.

Each step is reported as passed, failed or skipped. The outcome, the time and
the error category are stored on the connector and written to its audit log.

## 5. Credential encryption and key rotation

- Credentials and secret header values are encrypted with **AES-256-GCM**
  before they reach the database.
- Each value has a random 96-bit nonce. The connector id and the purpose (for
  example, which header) are bound in as associated data, so a ciphertext copied
  to another connector or field fails to decrypt.
- Each stored value has the form `v1:<key id>:<nonce>:<ciphertext>`. The key
  id is a fingerprint, never the key itself.
- The key, `CONNECTOR_ENCRYPTION_KEY`, is dedicated: startup refuses a key
  equal to `SECRET_KEY`.
- If the key is missing, the app still starts. Connectors can be listed, but
  creating, testing and executing return
  `503 CONNECTOR_ENCRYPTION_NOT_CONFIGURED`. Plaintext storage is never used,
  and the database rejects any credential value without the `v1:` prefix.
- The key is never logged, returned or sent to the browser. API responses
  carry only `has_credentials` and a fixed mask.

**Generate a key** (any OS with Python 3):

```bash
python -c "import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Windows without Python: `docker compose run --rm --no-deps backend python -c "…same…"`.

Store a copy in your password manager. **If the key is lost, the stored
credentials cannot be recovered.** Re-enter each connector's credentials after
setting a new key.

**Rotate the key:**

1. Generate a new key.
2. In `.env`, move the current key to `CONNECTOR_ENCRYPTION_PREVIOUS_KEYS` (if
   there are several, separate them with commas) and put the new key in
   `CONNECTOR_ENCRYPTION_KEY`.
3. Restart the backend: `docker compose up -d backend`. Old values still decrypt
   with the previous keys.
4. Re-encrypt everything with the new key:
   `docker compose exec backend python -m app.cli rotate-connector-keys`.
   Each changed connector gets a `credentials_rotated` audit event.
5. Empty `CONNECTOR_ENCRYPTION_PREVIOUS_KEYS` and restart again.

## 6. Network and SSRF protection: deployment requirements

A connector makes the OpsPilot server send requests, so a mistyped or malicious
URL could otherwise reach internal services. These controls apply on every
request, not only when the connector is saved:

- **Host allowlist.** `CONNECTOR_ALLOWED_HOSTS` lists exact hosts or
  `*.domain` patterns. It is empty by default, which means **no destination is
  allowed**.
- **HTTPS only.** `http://` is accepted only when `CONNECTOR_ALLOW_HTTP=true`
  *and* `APP_ENV` is development or test. Production refuses to start with it.
- **No credentials in URLs** (`user:pass@`), no fragments, no
  credential-like query parameters (`api_key`, `token`, `password` and similar).
  Credentials belong in headers.
- **Address checks.** Every address that DNS returns must be allowed:
  - Public addresses are allowed.
  - Private ranges (RFC 1918, CGNAT, unique-local IPv6) are refused unless
    listed in `CONNECTOR_ALLOWED_PRIVATE_NETWORKS`.
  - Loopback is allowed only in development or test, and only if listed.
  - Link-local addresses (including 169.254.169.254 cloud metadata), multicast,
    reserved and unspecified addresses are **never** allowed.
  - IPv4-mapped IPv6 addresses are checked as IPv4.
- **DNS rebinding.** OpsPilot resolves the host, validates the addresses, then
  connects to that validated IP. It sends the original `Host` header and TLS SNI,
  so certificate checks still use the host name, and a second DNS answer cannot
  redirect the connection.
- **No redirects** and **no proxies**, so neither can bypass these checks.

These are application controls. A production deployment should also:

1. Restrict the backend container's **egress** at the network layer (firewall,
   security group or Kubernetes NetworkPolicy) to the company API's address and
   port only.
2. Block the cloud metadata endpoint at the network layer as well.
3. Keep OpsPilot's own database on a network the connector cannot reach, as the
   `data` network in `docker-compose.yml` does. Never approve that network in
   `CONNECTOR_ALLOWED_PRIVATE_NETWORKS`.
4. Use a real certificate on the company API. Add an internal CA with
   `CONNECTOR_CA_BUNDLE` rather than using plain HTTP.

**Docker Desktop on Windows or macOS (local testing).** To reach an API on your
own PC from the backend container:

- Use `host.docker.internal` as the host and add it to `CONNECTOR_ALLOWED_HOSTS`.
- That name resolves to a private Docker address, usually in `192.168.65.0/24`.
  To check it, run `docker compose exec backend getent hosts host.docker.internal`.
  Add that network to `CONNECTOR_ALLOWED_PRIVATE_NETWORKS`.
- For a plain-HTTP test API, set `CONNECTOR_ALLOW_HTTP=true` with
  `APP_ENV=development`.

## 7. Security considerations of SQL in a GET query string

The company's existing contract puts SQL in the URL. OpsPilot supports this
deliberately and keeps GET, but URLs are logged in places request bodies are
not:

- **Web server and proxy access logs.** Nginx, IIS, load balancers, API
  gateways and WAFs record the full URL by default. On every hop to the company
  API, configure them to drop or redact the `sql` parameter, for example with a
  custom Nginx `log_format` that logs `$uri` instead of `$request_uri`.
- **APM and tracing tools** often capture the full URL. Configure their URL
  sanitisation.
- **URL length limits.** Servers and proxies limit URL length, often to 8 KB.
  Set `CONNECTOR_MAX_URL_LENGTH` and the connector's max SQL length to fit the
  lowest limit. Longer queries are refused, never truncated.
- **Caching.** The company API should send `Cache-Control: no-store`, and no
  shared cache should sit in front of it.
- **The query string is visible to anything on the path that terminates TLS.**
  This is another reason HTTPS is mandatory.

OpsPilot itself never writes SQL text to its logs or audit tables. It records
only a SHA-256 fingerprint and the length of the SQL, so an execution can be
matched with the company API's own records without storing the query.

## 8. Error categories

Every failure is reported with a category. It appears in the test result, on
the connector (as `last_error_category`) and in the audit log. Messages never
contain credentials, header values, SQL or response bodies.

| Category | Meaning |
| --- | --- |
| `configuration_invalid` | The stored configuration is unusable (for example, its schema was deleted). |
| `encryption_unavailable` | The key is missing, or a credential cannot be decrypted with the configured keys. |
| `destination_rejected` | The URL or a resolved address is not allowed by the SSRF policy. |
| `dns_resolution_failed` | The host could not be resolved. |
| `connection_failed` | TCP connection refused or reset. |
| `tls_failed` | Certificate or TLS handshake failure. |
| `timeout` | Connect, read or overall timeout exceeded. |
| `authentication_failed` | The API answered 401 or 403. |
| `redirect_rejected` | The API answered with a redirect. |
| `http_error` | Any other non-2xx status. |
| `response_too_large` | The body exceeded the size limit. |
| `invalid_content_type` | The response is not JSON. |
| `malformed_response` | The JSON does not match the response contract. |
| `external_api_error` | The envelope said `success: false`. |
| `request_too_large` | The SQL or URL exceeded its limit, so nothing was sent. |
| `validation_rejected` | The execution request was not a valid, sealed, trusted request. |

## 9. Trusted execution boundary

No public endpoint accepts SQL. The only way to call
`ConnectorService.execute` is with a `ValidatedQueryRequest`:

- The request is an immutable object carrying the connector, schema and version,
  the requesting user, the validation and permission references, and the exact
  SQL.
- It is sealed with HMAC-SHA256 under a random key that exists only in the
  running process.
- The adapter rejects anything that is unsealed, tampered with, expired (after
  120 s), from an unknown issuer, or for a connector that does not match.

In this module the only permitted issuer is the connection test, and it can seal
only the fixed test statement. A later SQL-validation module will add its own
issuer once its validation pipeline exists.

## 10. Permissions

Permissions are enforced by the dynamic permission engine (Module 04). The
Administrator role receives all six, and they can be granted to any role.

| Permission | Allows |
| --- | --- |
| `connector.read` | View connectors (masked), health, activity |
| `connector.create` | Create connectors |
| `connector.update` | Edit connectors and replace credentials |
| `connector.delete` | Delete connectors (stored ciphertext is erased) |
| `connector.test` | Run the connection test |
| `connector.activate` | Activate and deactivate |
