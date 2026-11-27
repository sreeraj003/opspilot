"""Unit tests of the connector building blocks (no database, no real network)."""

import asyncio
import base64
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.connectors.adapters.execution import ConnectorExecutionAdapter
from app.connectors.adapters.http_client import ConnectorHttpClient, Timeouts, encode_query
from app.connectors.adapters.response import ResponseContract, normalize, parse_json
from app.connectors.errors import ConnectorExecutionError, ErrorCategory, TrustedRequestRejectedError
from app.connectors.runtime import ConnectorRuntimeConfig
from app.connectors.security.credentials import (
    CredentialCipher,
    CredentialDecryptionError,
    EncryptionNotConfiguredError,
    parse_key,
    require_cipher,
)
from app.connectors.security.destinations import DestinationError, DestinationPolicy
from app.connectors.trusted import CONNECTOR_TEST_SQL, ExecutionApprovalAuthority, ValidatedQueryRequest
from tests.conftest import make_settings

KEY = b"k" * 32
OLD_KEY = b"o" * 32
CONNECTOR = uuid.uuid4()
HOST = "company-backend.example.com"
PUBLIC_IP = "93.184.216.34"


def b64(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode()


# --- credential encryption -------------------------------------------------------------------------------


def test_encrypt_decrypt_round_trip_and_fresh_nonces() -> None:
    cipher = CredentialCipher(KEY)
    first = cipher.encrypt("s3cret", connector_id=CONNECTOR, purpose="credentials:api_key")
    second = cipher.encrypt("s3cret", connector_id=CONNECTOR, purpose="credentials:api_key")
    assert first != second and first.startswith("v1:") and "s3cret" not in first
    assert cipher.decrypt(first, connector_id=CONNECTOR, purpose="credentials:api_key") == "s3cret"
    document = cipher.encrypt_json({"value": "x"}, connector_id=CONNECTOR, purpose="p")
    assert cipher.decrypt_json(document, connector_id=CONNECTOR, purpose="p") == {"value": "x"}


def test_ciphertext_is_bound_to_connector_and_purpose_and_tamper_evident() -> None:
    cipher = CredentialCipher(KEY)
    token = cipher.encrypt("s3cret", connector_id=CONNECTOR, purpose="credentials:api_key")
    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt(token, connector_id=uuid.uuid4(), purpose="credentials:api_key")
    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt(token, connector_id=CONNECTOR, purpose="credentials:bearer_token")
    version, kid, nonce, body = token.split(":")
    tampered = f"{version}:{kid}:{nonce}:{body[:-2]}{'A' if body[-2] != 'A' else 'B'}{body[-1]}"
    with pytest.raises(CredentialDecryptionError) as raised:
        cipher.decrypt(tampered, connector_id=CONNECTOR, purpose="credentials:api_key")
    assert "s3cret" not in str(raised.value)
    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt("plaintext-value", connector_id=CONNECTOR, purpose="credentials:api_key")
    with pytest.raises(CredentialDecryptionError):
        CredentialCipher(OLD_KEY).decrypt(token, connector_id=CONNECTOR, purpose="credentials:api_key")


def test_key_rotation_previous_keys_decrypt_primary_encrypts() -> None:
    old = CredentialCipher(OLD_KEY)
    token = old.encrypt("s3cret", connector_id=CONNECTOR, purpose="p")
    rotated = CredentialCipher(KEY, [OLD_KEY])
    assert rotated.decrypt(token, connector_id=CONNECTOR, purpose="p") == "s3cret"
    assert rotated.needs_rotation(token)
    fresh = rotated.encrypt("s3cret", connector_id=CONNECTOR, purpose="p")
    assert not rotated.needs_rotation(fresh) and fresh.split(":")[1] == rotated.primary_key_id
    assert "k" * 8 not in repr(rotated)


def test_key_parsing_and_settings() -> None:
    assert parse_key(b64(KEY)) == KEY
    assert parse_key(base64.b64encode(KEY).decode()) == KEY
    for bad in ("short", b64(b"x" * 16), "!!!!"):
        with pytest.raises(ValueError) as raised:
            parse_key(bad)
        assert bad not in str(raised.value)
    with pytest.raises(ValidationError) as invalid:
        make_settings(connector_encryption_key="not-a-key-value")
    assert "not-a-key-value" not in str(invalid.value)
    same = b64(b"s" * 48)
    with pytest.raises(ValidationError, match="must not be the same value as SECRET_KEY"):
        make_settings(connector_encryption_key=b64(KEY), secret_key=b64(KEY) + "")
    assert make_settings(secret_key=same, connector_encryption_key=b64(KEY)).connector_encryption_key is not None
    settings = make_settings(connector_encryption_key=b64(KEY), connector_encryption_previous_keys=f"{b64(OLD_KEY)}, ")
    cipher = CredentialCipher.from_settings(settings)
    assert cipher is not None and cipher.decrypt(
        CredentialCipher(OLD_KEY).encrypt("v", connector_id=CONNECTOR, purpose="p"), connector_id=CONNECTOR, purpose="p"
    ) == "v"


def test_missing_key_never_falls_back_to_plaintext() -> None:
    assert CredentialCipher.from_settings(make_settings()) is None
    with pytest.raises(EncryptionNotConfiguredError):
        require_cipher(None)


def test_production_rejects_http_and_loopback_networks() -> None:
    base = {"app_env": "production", "secret_key": "p" * 40, "database_url": "postgresql://u:p@db/x"}
    with pytest.raises(ValidationError, match="CONNECTOR_ALLOW_HTTP"):
        make_settings(**base, connector_allow_http=True)
    with pytest.raises(ValidationError, match="loopback"):
        make_settings(**base, connector_allowed_private_networks="127.0.0.0/8")
    production = make_settings(**base, connector_allowed_private_networks="10.0.0.0/8")
    assert production.connector_http_allowed is False and production.connector_loopback_allowed is False
    assert make_settings(connector_allow_http=True).connector_http_allowed is True
    with pytest.raises(ValidationError):
        make_settings(connector_allowed_hosts="bad host!")
    with pytest.raises(ValidationError):
        make_settings(connector_allowed_hosts="*.com")


# --- destinations (SSRF) ----------------------------------------------------------------------------------------


def _resolver(answers: dict[str, Sequence[str]]) -> Any:
    async def resolve(host: str, port: int) -> Sequence[str]:
        if host not in answers:
            raise OSError("not found")
        return answers[host]

    return resolve


def _policy(**kwargs: Any) -> DestinationPolicy:
    kwargs.setdefault("allowed_hosts", [HOST, "*.corp.example.com", "10.1.2.3"])
    kwargs.setdefault("resolver", _resolver({HOST: [PUBLIC_IP], "api.corp.example.com": ["10.9.9.9"]}))
    return DestinationPolicy(**kwargs)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://company-backend.example.com/x", "scheme_not_allowed"),
        ("file:///etc/passwd", "scheme_not_allowed"),
        ("https://user:pass@company-backend.example.com/x", "invalid_url"),
        ("https://company-backend.example.com/x?a=1", "invalid_url"),
        ("https://company-backend.example.com/x#frag", "invalid_url"),
        ("https://company-backend.example.com:99999/x", "invalid_url"),
        ("https://other.example.com/x", "host_not_allowed"),
        ("https://company-backend.example.com.evil.com/x", "host_not_allowed"),
        ("https://corp.example.com/x", "host_not_allowed"),
        ("https://localhost/x", "address_not_allowed"),
        ("https://10.1.2.3/x", "address_not_allowed"),
        ("https://[::1]/x", "host_not_allowed"),
        ("https://company backend.example.com/x", "invalid_url"),
    ],
)
def test_static_destination_checks(url: str, reason: str) -> None:
    with pytest.raises(DestinationError) as raised:
        _policy().check_url(url)
    assert raised.value.reason == reason


def test_allowed_destinations() -> None:
    policy = _policy(allowed_networks=["10.0.0.0/8"])
    assert policy.check_url("https://company-backend.example.com/api/sql").host == HOST
    assert policy.check_url("https://API.Corp.Example.com/sql").host == "api.corp.example.com"
    assert policy.check_url("https://10.1.2.3/x").ip_literal is not None
    assert _policy(allow_http=True).check_url("http://company-backend.example.com/x").port == 80


async def test_resolution_validates_every_address() -> None:
    policy = _policy()
    resolved = await policy.resolve("https://company-backend.example.com/x")
    assert str(resolved.address) == PUBLIC_IP
    with pytest.raises(DestinationError) as private:
        await policy.resolve("https://api.corp.example.com/x")
    assert private.value.reason == "address_not_allowed"
    assert "10.9.9.9" not in private.value.message

    for answers in ([PUBLIC_IP, "127.0.0.1"], ["169.254.169.254"], ["::ffff:10.0.0.1"], ["fe80::1"], ["0.0.0.0"], ["224.0.0.1"]):
        mixed = _policy(resolver=_resolver({HOST: answers}))
        with pytest.raises(DestinationError):
            await mixed.resolve("https://company-backend.example.com/x")
    with pytest.raises(DestinationError) as missing:
        await _policy(resolver=_resolver({})).resolve("https://company-backend.example.com/x")
    assert missing.value.reason == "dns_resolution_failed"
    with pytest.raises(DestinationError):
        await _policy(resolver=_resolver({HOST: []})).resolve("https://company-backend.example.com/x")


async def test_approved_private_networks_and_loopback_rules() -> None:
    approved = _policy(allowed_networks=["10.0.0.0/8", "169.254.0.0/16"])
    assert str((await approved.resolve("https://api.corp.example.com/x")).address) == "10.9.9.9"
    metadata = _policy(allowed_networks=["169.254.0.0/16"], resolver=_resolver({HOST: ["169.254.169.254"]}))
    with pytest.raises(DestinationError):
        await metadata.resolve("https://company-backend.example.com/x")  # link-local is never allowed
    loopback_answers = _resolver({HOST: ["127.0.0.1"]})
    with pytest.raises(DestinationError):
        await _policy(allowed_networks=["127.0.0.0/8"], resolver=loopback_answers).resolve("https://company-backend.example.com/x")
    dev = _policy(allowed_networks=["127.0.0.0/8"], allow_loopback=True, resolver=loopback_answers)
    assert str((await dev.resolve("https://company-backend.example.com/x")).address) == "127.0.0.1"


# --- request construction and the HTTP client ---------------------------------------------------------------------


def test_sql_is_percent_encoded_exactly() -> None:
    assert encode_query([("sql", "SELECT COUNT(*) AS total_orders FROM orders")]) == (
        "sql=SELECT%20COUNT%28%2A%29%20AS%20total_orders%20FROM%20orders"
    )
    assert encode_query([("q", "a+b&c=d#e/é")]) == "q=a%2Bb%26c%3Dd%23e%2F%C3%A9"


def _client(handler: Any, **kwargs: Any) -> ConnectorHttpClient:
    return ConnectorHttpClient(_policy(), transport=httpx.MockTransport(handler), **kwargs)


TIMEOUTS = Timeouts(connect=1, read=1, total=2)


async def test_client_pins_validated_address_and_keeps_host_name() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"success": True, "data": []}, headers={"set-cookie": "s=1; Domain=93.184.216.34"})

    client = _client(handler)
    try:
        sql = "SELECT COUNT(*) AS total_orders FROM orders"
        raw = await client.get(
            f"https://{HOST}/api/sql/execute", params=[("sql", sql)], headers={"X-API-Key": "k"}, timeouts=TIMEOUTS, max_bytes=1024
        )
        await client.get(f"https://{HOST}/api/sql/execute", params=[("sql", sql)], headers={}, timeouts=TIMEOUTS, max_bytes=1024)
    finally:
        await client.aclose()
    request = seen[0]
    assert str(request.url) == f"https://{PUBLIC_IP}/api/sql/execute?sql=SELECT%20COUNT%28%2A%29%20AS%20total_orders%20FROM%20orders"
    assert request.headers["host"] == HOST and request.extensions["sni_hostname"] == HOST
    assert request.headers["accept"] == "application/json" and request.method == "GET"
    assert "cookie" not in seen[1].headers  # cookies are never stored or replayed across requests
    assert raw.status_code == 200 and parse_json(raw.body) == {"success": True, "data": []}


async def test_client_refuses_long_urls_without_sending() -> None:
    sent: list[httpx.Request] = []
    client = _client(lambda r: sent.append(r) or httpx.Response(200, json=[]), max_url_length=600)
    try:
        with pytest.raises(ConnectorExecutionError) as raised:
            await client.get(f"https://{HOST}/x", params=[("sql", "x" * 700)], headers={}, timeouts=TIMEOUTS, max_bytes=1024)
    finally:
        await client.aclose()
    assert raised.value.category is ErrorCategory.REQUEST_TOO_LARGE and not sent


async def test_client_streams_and_stops_at_the_size_limit() -> None:
    async def stream() -> Any:
        for _ in range(100):
            yield b"x" * 1024

    client = _client(lambda r: httpx.Response(200, content=stream(), headers={"content-type": "application/json"}))
    try:
        with pytest.raises(ConnectorExecutionError) as raised:
            await client.get(f"https://{HOST}/x", params=[], headers={}, timeouts=TIMEOUTS, max_bytes=4096)
    finally:
        await client.aclose()
    assert raised.value.category is ErrorCategory.RESPONSE_TOO_LARGE


async def test_client_declared_length_too_large() -> None:
    client = _client(lambda r: httpx.Response(200, content=b"[]", headers={"content-type": "application/json", "content-length": "99999999"}))
    try:
        with pytest.raises(ConnectorExecutionError) as raised:
            await client.get(f"https://{HOST}/x", params=[], headers={}, timeouts=TIMEOUTS, max_bytes=4096)
    finally:
        await client.aclose()
    assert raised.value.category is ErrorCategory.RESPONSE_TOO_LARGE


async def test_client_overall_timeout() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=[])

    client = _client(slow)
    try:
        with pytest.raises(ConnectorExecutionError) as raised:
            await client.get(f"https://{HOST}/x", params=[], headers={}, timeouts=Timeouts(connect=1, read=1, total=0.2), max_bytes=1024)
    finally:
        await client.aclose()
    assert raised.value.category is ErrorCategory.TIMEOUT


async def test_redirects_are_not_followed() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(307, headers={"location": "https://127.0.0.1/admin"})

    client = _client(handler)
    try:
        with pytest.raises(ConnectorExecutionError) as raised:
            await client.get(f"https://{HOST}/x", params=[], headers={}, timeouts=TIMEOUTS, max_bytes=1024)
    finally:
        await client.aclose()
    assert raised.value.category is ErrorCategory.REDIRECT_REJECTED and len(sent) == 1


def test_production_client_verifies_tls() -> None:
    import ssl

    from app.connectors.adapters.http_client import build_ssl_context

    context = build_ssl_context(None)
    assert context.verify_mode is ssl.CERT_REQUIRED and context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


# --- response validation ------------------------------------------------------------------------------------------------


ENVELOPE = ResponseContract()


def test_reference_envelope_is_normalised() -> None:
    document = {"success": True, "data": [{"total_orders": 1250}], "metadata": {"execution_time_ms": 45}}
    result = normalize(document, ENVELOPE, http_status=200, size=80)
    assert result.columns == ["total_orders"] and result.rows == [{"total_orders": 1250}]
    assert result.row_count == 1 and result.execution_time_ms == 45 and not result.is_empty


def test_columns_and_values_are_preserved_without_fabrication() -> None:
    rows = [{"b": 1, "a": None}, {"a": "x", "c": [1, 2]}]
    result = normalize({"success": True, "data": rows}, ENVELOPE, http_status=200, size=1)
    assert result.columns == ["b", "a", "c"]
    assert result.rows == rows and "c" not in result.rows[0]


def test_empty_result_is_distinct_from_errors() -> None:
    assert normalize({"success": True, "data": []}, ENVELOPE, http_status=200, size=1).is_empty
    with pytest.raises(ConnectorExecutionError) as failed:
        normalize({"success": False, "data": []}, ENVELOPE, http_status=200, size=1)
    assert failed.value.category is ErrorCategory.EXTERNAL_API_ERROR


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"data": []},
        {"success": "true", "data": []},
        {"success": 1, "data": []},
        {"success": True},
        {"success": True, "data": None},
        {"success": True, "data": {"a": 1}},
        {"success": True, "data": [1, 2]},
        {"success": True, "data": [], "metadata": "fast"},
    ],
)
def test_malformed_envelopes_are_rejected(document: Any) -> None:
    with pytest.raises(ConnectorExecutionError) as raised:
        normalize(document, ENVELOPE, http_status=200, size=1)
    assert raised.value.category is ErrorCategory.MALFORMED_RESPONSE


def test_configured_field_names_and_raw_arrays() -> None:
    custom = ResponseContract(success_field="ok", data_field="rows", metadata_field="meta")
    assert normalize({"ok": True, "rows": [{"a": 1}]}, custom, http_status=200, size=1).row_count == 1
    with pytest.raises(ConnectorExecutionError):
        normalize({"success": True, "data": [{"a": 1}]}, custom, http_status=200, size=1)  # never guesses
    raw = ResponseContract(format="raw_array")
    assert normalize([{"a": 1}], raw, http_status=200, size=1).columns == ["a"]
    with pytest.raises(ConnectorExecutionError):
        normalize({"success": True, "data": []}, raw, http_status=200, size=1)
    with pytest.raises(ValidationError):
        ResponseContract(data_field="data; drop")


@pytest.mark.parametrize("body", [b"{", b"NaN", b'{"a": NaN}', b"\xff\xfe", b"[" * 100_000])
def test_invalid_json_is_rejected(body: bytes) -> None:
    with pytest.raises(ConnectorExecutionError) as raised:
        parse_json(body)
    assert raised.value.category is ErrorCategory.MALFORMED_RESPONSE


# --- the trusted execution boundary ----------------------------------------------------------------------------------------


SOURCE = uuid.uuid4()
USER = uuid.uuid4()


def _config(**overrides: Any) -> ConnectorRuntimeConfig:
    values: dict[str, Any] = {
        "connector_id": CONNECTOR,
        "schema_source_id": SOURCE,
        "current_schema_version": 3,
        "is_active": True,
        "url": f"https://{HOST}/api/sql",
        "sql_parameter_name": "sql",
        "extra_query_params": (),
        "contract": ResponseContract(),
        "timeouts": TIMEOUTS,
        "max_response_size_bytes": 4096,
        "max_sql_length": 4000,
        "headers": {"X-API-Key": "secret-in-config"},
    }
    values.update(overrides)
    return ConnectorRuntimeConfig(**values)


def _test_request(authority: ExecutionApprovalAuthority, **overrides: Any) -> ValidatedQueryRequest:
    values = {"connector_id": CONNECTOR, "requesting_user_id": USER, "schema_source_id": SOURCE, "schema_version": 3}
    values.update(overrides)
    return authority.issue_connector_test(**values)


async def _adapter() -> tuple[ConnectorExecutionAdapter, ExecutionApprovalAuthority, list[httpx.Request], ConnectorHttpClient]:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"success": True, "data": [{"opspilot_connection_test": 1}]})

    authority = ExecutionApprovalAuthority()
    http = _client(handler)
    return ConnectorExecutionAdapter(http, authority), authority, sent, http


async def test_adapter_executes_sealed_test_request() -> None:
    adapter, authority, sent, http = await _adapter()
    try:
        result = await adapter.execute(_test_request(authority), _config())
    finally:
        await http.aclose()
    assert result.rows == [{"opspilot_connection_test": 1}]
    assert sent[0].url.params["sql"] == CONNECTOR_TEST_SQL
    assert "secret-in-config" not in repr(_config())


async def test_raw_sql_and_forged_requests_are_refused() -> None:
    adapter, authority, sent, http = await _adapter()
    genuine = _test_request(authority)
    forgeries: list[Any] = [
        "DROP TABLE orders",
        {"exact_sql": CONNECTOR_TEST_SQL},
        genuine.model_copy(update={"exact_sql": "SELECT * FROM salaries"}),
        genuine.model_copy(update={"issuer": "sql_guard"}),
        ValidatedQueryRequest.model_construct(**{**genuine.model_dump(), "exact_sql": "DELETE FROM orders"}),
        _test_request(ExecutionApprovalAuthority()),  # sealed by another process's authority
    ]
    try:
        for forged in forgeries:
            with pytest.raises(TrustedRequestRejectedError):
                await adapter.execute(forged, _config())
    finally:
        await http.aclose()
    assert sent == []


async def test_only_permitted_issuers_and_fixed_sql() -> None:
    authority = ExecutionApprovalAuthority()
    other = authority._issue(  # an issuer that is not permitted
        issuer="reporting_module", connector_id=CONNECTOR, exact_sql="SELECT 1", requesting_user_id=USER,
        schema_source_id=SOURCE, schema_version=3, validation_reference="guard:1", permission_context_reference="perm:1",
    )
    with pytest.raises(TrustedRequestRejectedError, match="cannot be executed yet"):
        authority.verify(other)
    # The SQL security guard (Module 11) is permitted, but only with a bound approval.
    unbound = authority._issue(
        issuer="sql_security_guard", connector_id=CONNECTOR, exact_sql="SELECT 1", requesting_user_id=USER,
        schema_source_id=SOURCE, schema_version=3, validation_reference="guard:1", permission_context_reference="perm:1",
    )
    with pytest.raises(TrustedRequestRejectedError, match="not bound to a security approval"):
        authority.verify(unbound)
    arbitrary = authority._issue(
        issuer="connector_test", connector_id=CONNECTOR, exact_sql="SELECT * FROM users", requesting_user_id=USER,
        schema_source_id=SOURCE, schema_version=3, validation_reference="x", permission_context_reference="y",
    )
    with pytest.raises(TrustedRequestRejectedError, match="fixed system query"):
        authority.verify(arbitrary)


async def test_request_must_match_connector_schema_version_and_age() -> None:
    adapter, authority, sent, http = await _adapter()
    try:
        for request, config in [
            (_test_request(authority, connector_id=uuid.uuid4()), _config()),
            (_test_request(authority, schema_source_id=uuid.uuid4()), _config()),
            (_test_request(authority, schema_version=2), _config()),
        ]:
            with pytest.raises(TrustedRequestRejectedError):
                await adapter.execute(request, config)
        with pytest.raises(ConnectorExecutionError) as too_long:
            await adapter.execute(_test_request(authority), _config(max_sql_length=10))
        assert too_long.value.category is ErrorCategory.REQUEST_TOO_LARGE
    finally:
        await http.aclose()
    assert sent == []
    request = _test_request(authority)
    with pytest.raises(TrustedRequestRejectedError, match="expired"):
        authority.verify(request, now=datetime.now(UTC) + timedelta(minutes=5))
    assert "opspilot_connection_test" not in repr(request)
