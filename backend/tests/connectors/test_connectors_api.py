"""Connector configuration, credentials, testing, audit and authorization through the HTTP API."""

import json
import logging
from typing import Any
from urllib.parse import unquote

import httpx
import pytest

from tests.connectors.conftest import (
    API_KEY,
    BEARER,
    COMPANY_HOST,
    COMPANY_IP,
    Connectors,
    envelope,
)

pytestmark = pytest.mark.postgres

TEST_SQL = "SELECT 1 AS opspilot_connection_test"


def _no_secret(payload: Any, *secrets: str) -> None:
    text = json.dumps(payload)
    for secret in secrets:
        assert secret not in text


# --- create, read, masking ---------------------------------------------------------------------------------


def test_create_returns_masked_configuration_and_stores_ciphertext(connectors: Connectors) -> None:
    schema = connectors.schema()
    created = connectors.create(schema["id"], description="Reporting replica")

    assert created["name"] == "Company SQL API"
    assert created["http_method"] == "GET" and created["sql_parameter_name"] == "sql"
    assert created["authentication_type"] == "api_key" and created["auth_header_name"] == "X-API-Key"
    assert created["has_credentials"] is True and created["credential_mask"] == "••••••••"
    assert created["endpoint_host"] == COMPANY_HOST
    assert created["schema_source_name"] == schema["name"]
    assert created["is_active"] is False and created["last_test_status"] == "untested"
    assert created["destination_allowed"] is True
    assert "credential" not in created and "encrypted_credentials" not in created
    _no_secret(created, API_KEY)

    row = connectors.stored(created["id"])
    assert row.encrypted_credentials.startswith("v1:")
    assert API_KEY not in row.encrypted_credentials
    # Read endpoints are masked too.
    _no_secret(connectors.ok(connectors.get(f"/connectors/{created['id']}")), API_KEY, row.encrypted_credentials)
    _no_secret(connectors.ok(connectors.get("/connectors")), API_KEY, row.encrypted_credentials)


def test_create_with_secret_custom_headers(connectors: Connectors) -> None:
    created = connectors.create(
        authentication_type="bearer_token",
        credential=BEARER,
        custom_headers=[
            {"name": "X-Tenant", "value": "acme"},
            {"name": "X-Signing-Secret", "value": "header-secret-789", "secret": True},
        ],
        extra_query_params=[{"name": "format", "value": "json"}],
    )
    assert created["custom_headers"] == [
        {"name": "X-Tenant", "secret": False, "value": "acme", "has_value": True},
        {"name": "X-Signing-Secret", "secret": True, "value": None, "has_value": True},
    ]
    assert created["extra_query_params"] == [{"name": "format", "value": "json"}]
    row = connectors.stored(created["id"])
    assert "header-secret-789" not in json.dumps(row.custom_headers)
    _no_secret(created, BEARER, "header-secret-789")


@pytest.mark.parametrize(
    ("extra", "status", "code"),
    [
        ({"base_url": "http://company-backend.example.com"}, 422, "CONNECTOR_DESTINATION_REJECTED"),
        ({"base_url": "https://evil.example.org"}, 422, "CONNECTOR_DESTINATION_REJECTED"),
        ({"base_url": "https://10.1.2.3"}, 422, "CONNECTOR_DESTINATION_REJECTED"),
        ({"base_url": "https://user:pw@company-backend.example.com"}, 422, "CONNECTOR_DESTINATION_REJECTED"),
        ({"base_url": "https://localhost"}, 422, "CONNECTOR_DESTINATION_REJECTED"),
        ({"base_url": "ftp://company-backend.example.com"}, 422, "CONNECTOR_DESTINATION_REJECTED"),
        ({"endpoint_path": "/api/../admin"}, 422, "VALIDATION_ERROR"),
        ({"endpoint_path": "/api/sql?x=1"}, 422, "VALIDATION_ERROR"),
        ({"http_method": "POST"}, 422, "VALIDATION_ERROR"),
        ({"sql_parameter_name": "bad name"}, 422, "VALIDATION_ERROR"),
        ({"authentication_type": "api_key", "credential": None}, 422, "CONNECTOR_CONFIGURATION_INVALID"),
        ({"authentication_type": "none"}, 422, "CONNECTOR_CONFIGURATION_INVALID"),
        ({"credential": "has\nnewline"}, 422, "VALIDATION_ERROR"),
        ({"custom_headers": [{"name": "Host", "value": "x"}]}, 422, "VALIDATION_ERROR"),
        ({"custom_headers": [{"name": "X-A", "value": "bad\r\nInjected: 1"}]}, 422, "VALIDATION_ERROR"),
        ({"custom_headers": [{"name": "X-API-Key", "value": "dup"}]}, 422, "CONNECTOR_CONFIGURATION_INVALID"),
        ({"extra_query_params": [{"name": "api_token", "value": "x"}]}, 422, "CONNECTOR_CONFIGURATION_INVALID"),
        ({"extra_query_params": [{"name": "sql", "value": "x"}]}, 422, "CONNECTOR_CONFIGURATION_INVALID"),
        ({"timeout_seconds": 10, "read_timeout_seconds": 20}, 422, "CONNECTOR_CONFIGURATION_INVALID"),
        ({"timeout_seconds": 500}, 422, "CONNECTOR_CONFIGURATION_INVALID"),
        ({"unknown_field": 1}, 422, "VALIDATION_ERROR"),
    ],
)
def test_invalid_configuration_is_rejected(connectors: Connectors, extra: dict[str, Any], status: int, code: str) -> None:
    schema = connectors.schema()
    response = connectors.post("/connectors", connectors.body(schema["id"], **extra))
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert API_KEY not in response.text and "has\nnewline" not in response.text


def test_unknown_or_deleted_schema_is_rejected(connectors: Connectors) -> None:
    response = connectors.post("/connectors", connectors.body("00000000-0000-0000-0000-000000000000"))
    assert response.status_code == 422
    schema = connectors.schema()
    connectors.ok(connectors.delete(f"/schemas/{schema['id']}"), 204)
    assert connectors.post("/connectors", connectors.body(schema["id"])).status_code == 422


def test_duplicate_name_and_endpoint_conflict(connectors: Connectors) -> None:
    schema = connectors.schema()
    connectors.create(schema["id"])
    same_name = connectors.post("/connectors", connectors.body(schema["id"], endpoint_path="/other"))
    assert same_name.status_code == 409
    same_url = connectors.post(
        "/connectors", connectors.body(schema["id"], name="Other", base_url=f"https://{COMPANY_HOST}/api", endpoint_path="/sql/execute")
    )
    assert same_url.status_code == 409 and "endpoint" in same_url.json()["error"]["message"]


def test_missing_encryption_key_fails_securely(connectors_factory: Any) -> None:
    harness: Connectors = connectors_factory(connector_encryption_key=None)
    schema = harness.schema()
    response = harness.post("/connectors", harness.body(schema["id"]))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CONNECTOR_ENCRYPTION_NOT_CONFIGURED"
    assert harness.api.sql("SELECT count(*) FROM external_api_connectors")[0][0] == 0
    # A connector without secrets needs no key.
    created = harness.create(schema["id"], authentication_type="none", credential=None)
    assert created["has_credentials"] is False
    assert harness.ok(harness.get("/connectors/environment"))["encryption_configured"] is False


def test_list_search_filter_and_paginate(connectors: Connectors) -> None:
    first = connectors.schema()
    second = connectors.schema()
    a = connectors.create(first["id"], name="Alpha warehouse")
    connectors.create(second["id"], name="Beta finance", endpoint_path="/finance/sql", authentication_type="none", credential=None)
    connectors.ok(connectors.patch(f"/connectors/{a['id']}/status", {"is_active": True}))

    def names(query: str) -> list[str]:
        return [c["name"] for c in connectors.ok(connectors.get(f"/connectors?{query}"))["items"]]

    assert names("") == ["Alpha warehouse", "Beta finance"]
    assert names("search=beta") == ["Beta finance"]
    assert names("status=active") == ["Alpha warehouse"]
    assert names("status=inactive") == ["Beta finance"]
    assert names("authentication_type=none") == ["Beta finance"]
    assert names(f"schema_source_id={first['id']}") == ["Alpha warehouse"]
    assert names("last_test_status=untested") == ["Alpha warehouse", "Beta finance"]
    page = connectors.ok(connectors.get("/connectors?limit=1&offset=1"))
    assert page["total"] == 2 and [c["name"] for c in page["items"]] == ["Beta finance"]
    assert names("sort=name&order=desc") == ["Beta finance", "Alpha warehouse"]
    assert connectors.get("/connectors/00000000-0000-0000-0000-000000000000").status_code == 404


# --- update and credential handling --------------------------------------------------------------------------


def test_update_keeps_omitted_credential_and_replaces_given_one(connectors: Connectors) -> None:
    created = connectors.create()
    before = connectors.stored(created["id"]).encrypted_credentials

    updated = connectors.ok(connectors.patch(f"/connectors/{created['id']}", {"description": "Renamed"}))
    assert updated["description"] == "Renamed" and updated["has_credentials"]
    assert connectors.stored(created["id"]).encrypted_credentials == before

    connectors.ok(connectors.patch(f"/connectors/{created['id']}", {"credential": "new-secret-value"}))
    after = connectors.stored(created["id"]).encrypted_credentials
    assert after != before and "new-secret-value" not in after
    connectors.test(created["id"])
    assert connectors.company.last.headers["X-API-Key"] == "new-secret-value"

    audit = connectors.audit(created["id"])
    assert [e.operation_type for e in audit][:3] == ["created", "updated", "updated"]
    assert "credential" in audit[2].details["changed_fields"]
    _no_secret([e.details for e in audit], "new-secret-value", API_KEY)


def test_changing_authentication_type_requires_a_new_credential(connectors: Connectors) -> None:
    created = connectors.create()
    response = connectors.patch(f"/connectors/{created['id']}", {"authentication_type": "bearer_token"})
    assert response.status_code == 422
    updated = connectors.ok(
        connectors.patch(f"/connectors/{created['id']}", {"authentication_type": "bearer_token", "credential": BEARER})
    )
    assert updated["authentication_type"] == "bearer_token" and updated["auth_header_name"] is None
    none = connectors.ok(connectors.patch(f"/connectors/{created['id']}", {"authentication_type": "none"}))
    assert none["has_credentials"] is False
    assert connectors.stored(created["id"]).encrypted_credentials is None


def test_secret_header_without_value_keeps_stored_secret(connectors: Connectors) -> None:
    created = connectors.create(custom_headers=[{"name": "X-Signature", "value": "sig-secret", "secret": True}])
    connectors.ok(
        connectors.patch(
            f"/connectors/{created['id']}",
            {"custom_headers": [{"name": "X-Signature", "secret": True}, {"name": "X-Extra", "value": "1"}]},
        )
    )
    connectors.test(created["id"])
    assert connectors.company.last.headers["X-Signature"] == "sig-secret"
    assert connectors.company.last.headers["X-Extra"] == "1"
    missing = connectors.patch(f"/connectors/{created['id']}", {"custom_headers": [{"name": "X-New", "secret": True}]})
    assert missing.status_code == 422


def test_connection_changes_reset_test_status(connectors: Connectors) -> None:
    created = connectors.create()
    assert connectors.test(created["id"])["status"] == "passed"
    assert connectors.ok(connectors.get(f"/connectors/{created['id']}"))["last_test_status"] == "passed"
    connectors.ok(connectors.patch(f"/connectors/{created['id']}", {"description": "no reset"}))
    assert connectors.ok(connectors.get(f"/connectors/{created['id']}"))["last_test_status"] == "passed"
    connectors.ok(connectors.patch(f"/connectors/{created['id']}", {"endpoint_path": "/v2/sql"}))
    assert connectors.ok(connectors.get(f"/connectors/{created['id']}"))["last_test_status"] == "untested"


def test_update_revalidates_destination(connectors: Connectors) -> None:
    created = connectors.create()
    response = connectors.patch(f"/connectors/{created['id']}", {"base_url": "https://169.254.169.254"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CONNECTOR_DESTINATION_REJECTED"


def test_delete_soft_deletes_and_erases_ciphertext(connectors: Connectors) -> None:
    created = connectors.create(custom_headers=[{"name": "X-Sig", "value": "s3cr3t-h", "secret": True}])
    connectors.ok(connectors.delete(f"/connectors/{created['id']}"), 204)
    assert connectors.get(f"/connectors/{created['id']}").status_code == 404
    row = connectors.stored(created["id"])
    assert row.deleted_at is not None and row.encrypted_credentials is None and row.custom_headers == []
    # The name can be used again.
    connectors.create(name="Company SQL API")
    assert [e.operation_type for e in connectors.audit(created["id"])] == ["created", "deleted"]


# --- activation ---------------------------------------------------------------------------------------------------


def test_activate_and_deactivate(connectors: Connectors) -> None:
    schema = connectors.schema()
    first = connectors.create(schema["id"])
    second = connectors.create(schema["id"], name="Second", endpoint_path="/second")
    assert connectors.ok(connectors.patch(f"/connectors/{first['id']}/status", {"is_active": True}))["is_active"] is True
    conflict = connectors.patch(f"/connectors/{second['id']}/status", {"is_active": True})
    assert conflict.status_code == 409
    connectors.ok(connectors.patch(f"/connectors/{first['id']}/status", {"is_active": False}))
    connectors.ok(connectors.patch(f"/connectors/{second['id']}/status", {"is_active": True}))
    operations = [e.operation_type for e in connectors.audit(first["id"])]
    assert operations == ["created", "activated", "deactivated"]


def test_inactive_schema_cannot_be_activated(connectors: Connectors) -> None:
    schema = connectors.schema()
    created = connectors.create(schema["id"])
    connectors.ok(connectors.patch(f"/schemas/{schema['id']}", {"is_active": False}))
    assert connectors.patch(f"/connectors/{created['id']}/status", {"is_active": True}).status_code == 422


# --- connection test -------------------------------------------------------------------------------------------------


def test_connection_test_sends_fixed_query_with_encoding_auth_and_pinned_ip(connectors: Connectors) -> None:
    created = connectors.create(extra_query_params=[{"name": "format", "value": "json"}])
    result = connectors.test(created["id"])

    assert result["status"] == "passed" and result["method"] == "test_query" and result["row_count"] == 1
    assert [s["status"] for s in result["steps"]] == ["passed"] * 6
    request = connectors.company.last
    assert request.method == "GET"
    assert request.url.host == COMPANY_IP  # connected to the validated address
    assert request.headers["host"] == COMPANY_HOST
    assert request.extensions["sni_hostname"] == COMPANY_HOST
    assert request.url.path == "/api/sql/execute"
    assert request.url.query == b"format=json&sql=SELECT%201%20AS%20opspilot_connection_test"
    assert unquote(request.url.params["sql"]) == TEST_SQL
    assert request.headers["X-API-Key"] == API_KEY
    assert API_KEY not in str(request.url)
    assert request.content == b""
    _no_secret(result, API_KEY)

    stored = connectors.ok(connectors.get(f"/connectors/{created['id']}"))
    assert stored["last_test_status"] == "passed" and stored["last_tested_at"] is not None

    event = connectors.audit(created["id"])[-1]
    assert event.operation_type == "tested" and event.status == "success"
    assert event.sql_length == len(TEST_SQL) and len(event.sql_fingerprint) == 64
    assert event.execution_reference == result["execution_reference"]
    assert event.duration_ms is not None and event.response_size_bytes > 0 and event.row_count == 1
    assert TEST_SQL not in json.dumps(event.details)


def test_connection_test_ignores_any_sql_in_the_request(connectors: Connectors) -> None:
    created = connectors.create()
    response = connectors.client.post(
        f"/api/v1/connectors/{created['id']}/test", json={"sql": "DROP TABLE orders"}, headers=connectors.admin
    )
    assert response.status_code == 200
    assert connectors.company.last.url.params["sql"] == TEST_SQL


def test_bearer_authentication_header(connectors: Connectors) -> None:
    created = connectors.create(authentication_type="bearer_token", credential=BEARER)
    connectors.test(created["id"])
    assert connectors.company.last.headers["Authorization"] == f"Bearer {BEARER}"


def test_custom_header_authentication(connectors: Connectors) -> None:
    created = connectors.create(authentication_type="custom_header", auth_header_name="X-Company-Auth", credential="custom-cred")
    connectors.test(created["id"])
    assert connectors.company.last.headers["X-Company-Auth"] == "custom-cred"


def test_health_endpoint_is_used_when_configured(connectors: Connectors) -> None:
    created = connectors.create(health_check_path="/api/health")
    connectors.company.reply(200, {"status": "ok"})
    result = connectors.test(created["id"])
    assert result["status"] == "passed" and result["method"] == "health_endpoint"
    assert connectors.company.last.url.path == "/api/health" and "sql" not in connectors.company.last.url.params
    assert connectors.company.last.headers["X-API-Key"] == API_KEY


def _failed(connectors: Connectors, setup: Any) -> dict[str, Any]:
    created = connectors.create()
    setup(connectors)
    result = connectors.test(created["id"])
    assert result["status"] == "failed"
    stored = connectors.ok(connectors.get(f"/connectors/{created['id']}"))
    assert stored["last_test_status"] == "failed"
    assert stored["last_error_category"] == result["error_category"]
    event = connectors.audit(created["id"])[-1]
    assert event.status == "failure" and event.error_category == result["error_category"]
    _no_secret(result, API_KEY)
    _no_secret(stored, API_KEY)
    return result


def _failed_step(result: dict[str, Any]) -> str:
    return next(s["name"] for s in result["steps"] if s["status"] == "failed")


@pytest.mark.parametrize(
    ("setup", "category", "step"),
    [
        (lambda c: c.company.reply(401, {"success": False}), "authentication_failed", "authentication"),
        (lambda c: c.company.reply(403, {"success": False}), "authentication_failed", "authentication"),
        (lambda c: c.company.reply(500, {"success": False}), "http_error", "response_format"),
        (lambda c: c.company.reply(302, headers={"location": "http://169.254.169.254/"}), "redirect_rejected", "response_format"),
        (lambda c: c.company.reply(200, raw=b"<html>ok</html>", content_type="text/html"), "invalid_content_type", "response_format"),
        (lambda c: c.company.reply(200, raw=b"{not json"), "malformed_response", "response_format"),
        (lambda c: c.company.reply(200, {"rows": []}), "malformed_response", "response_format"),
        (lambda c: c.company.reply(200, {"success": False, "error": {"code": "SQL_ERROR", "message": "secret table x"}}),
         "external_api_error", "response_format"),
        (lambda c: c.company.reply(200, raw=b"[" + b"1," * 3_000_000 + b"1]"), "response_too_large", "response_format"),
        (lambda c: c.company.fail(httpx.ReadTimeout("timed out")), "timeout", "network"),
        (lambda c: c.company.fail(httpx.ConnectError("refused")), "connection_failed", "network"),
        (lambda c: c.company.fail(httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")),
         "tls_failed", "tls"),
        (lambda c: c.dns.answers.clear(), "dns_resolution_failed", "network"),
        (lambda c: c.dns.answers.update({COMPANY_HOST: ["10.0.0.5"]}), "destination_rejected", "destination"),
        (lambda c: c.dns.answers.update({COMPANY_HOST: [COMPANY_IP, "127.0.0.1"]}), "destination_rejected", "destination"),
        (lambda c: c.dns.answers.update({COMPANY_HOST: ["169.254.169.254"]}), "destination_rejected", "destination"),
    ],
)
def test_connection_test_failures_are_categorised(connectors: Connectors, setup: Any, category: str, step: str) -> None:
    result = _failed(connectors, setup)
    assert result["error_category"] == category
    assert _failed_step(result) == step
    statuses = [s["status"] for s in result["steps"]]
    assert statuses[statuses.index("failed") + 1:] == ["skipped"] * (len(statuses) - statuses.index("failed") - 1)
    assert "secret table x" not in json.dumps(result)


def test_external_error_code_is_reported_but_not_its_message(connectors: Connectors) -> None:
    result = _failed(connectors, lambda c: c.company.reply(200, {"success": False, "error": {"code": "SQL_ERROR", "message": "x"}}))
    assert "SQL_ERROR" in result["message"]


def test_empty_result_is_success_not_error(connectors: Connectors) -> None:
    created = connectors.create()
    connectors.company.reply(200, envelope([]))
    result = connectors.test(created["id"])
    assert result["status"] == "passed" and result["row_count"] == 0


def test_raw_array_response_contract(connectors: Connectors) -> None:
    created = connectors.create(response_contract={"format": "raw_array"})
    connectors.company.reply(200, [{"opspilot_connection_test": 1}])
    assert connectors.test(created["id"])["status"] == "passed"
    connectors.company.reply(200, envelope())
    assert connectors.test(created["id"])["error_category"] == "malformed_response"


def test_test_without_encryption_key_reports_configuration_failure(connectors_factory: Any, company: Any) -> None:
    harness: Connectors = connectors_factory()
    created = harness.create()
    # A second process without the key (or with a different one) cannot use the stored secret.
    other: Connectors = connectors_factory(harness, connector_encryption_key=None)
    result = other.test(created["id"], headers=other.admin)
    assert result["status"] == "failed" and result["error_category"] == "encryption_unavailable"
    assert _failed_step(result) == "configuration"
    assert len(company.requests) == 0


def test_removed_host_is_refused_before_connecting(connectors_factory: Any, company: Any) -> None:
    harness: Connectors = connectors_factory()
    created = harness.create()
    narrowed: Connectors = connectors_factory(harness, connector_allowed_hosts=["other.example.com"])
    result = narrowed.test(created["id"], headers=narrowed.admin)
    assert result["error_category"] == "destination_rejected" and len(company.requests) == 0
    detail = narrowed.ok(narrowed.get(f"/connectors/{created['id']}"))
    assert detail["destination_allowed"] is False and detail["destination_problem"]


# --- health, audit, environment ---------------------------------------------------------------------------------------


def test_health_reports_readiness(connectors: Connectors) -> None:
    created = connectors.create()
    path = f"/connectors/{created['id']}/health"
    assert connectors.ok(connectors.get(path))["status"] == "inactive"
    connectors.ok(connectors.patch(f"/connectors/{created['id']}/status", {"is_active": True}))
    assert connectors.ok(connectors.get(path))["status"] == "untested"
    connectors.test(created["id"])
    health = connectors.ok(connectors.get(path))
    assert health["status"] == "ready" and health["credentials_available"] and health["destination_allowed"]
    connectors.company.reply(500)
    connectors.test(created["id"])
    assert connectors.ok(connectors.get(path))["status"] == "failing"
    requests_before = len(connectors.company.requests)
    connectors.get(path)
    assert len(connectors.company.requests) == requests_before  # health makes no network call


def test_audit_history_endpoint(connectors: Connectors) -> None:
    created = connectors.create()
    connectors.test(created["id"])
    page = connectors.ok(connectors.get(f"/connectors/{created['id']}/audit"))
    assert [e["operation_type"] for e in page["items"]] == ["tested", "created"]
    assert page["items"][0]["requesting_user_name"] == "Installation Admin"
    assert "sql_fingerprint" not in page["items"][0]
    tested = connectors.ok(connectors.get(f"/connectors/{created['id']}/audit?operation_type=tested"))
    assert tested["total"] == 1
    _no_secret(page, API_KEY, TEST_SQL)


def test_environment_lists_server_settings_without_secrets(connectors: Connectors) -> None:
    environment = connectors.ok(connectors.get("/connectors/environment"))
    assert environment["encryption_configured"] is True
    assert COMPANY_HOST in environment["allowed_hosts"]
    assert environment["test_query"] == TEST_SQL
    assert environment["http_allowed"] is False


def test_secrets_never_reach_logs(connectors: Connectors, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    created = connectors.create()
    connectors.test(created["id"])
    connectors.company.reply(401)
    connectors.test(created["id"])
    text = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert API_KEY not in text
    assert TEST_SQL not in text


# --- authorization -------------------------------------------------------------------------------------------------------


def test_every_endpoint_requires_authentication(connectors: Connectors) -> None:
    created = connectors.create()
    cid = created["id"]
    for method, path in [
        ("GET", "/connectors"), ("POST", "/connectors"), ("GET", "/connectors/environment"), ("GET", f"/connectors/{cid}"),
        ("PATCH", f"/connectors/{cid}"), ("DELETE", f"/connectors/{cid}"), ("PATCH", f"/connectors/{cid}/status"),
        ("POST", f"/connectors/{cid}/test"), ("GET", f"/connectors/{cid}/health"), ("GET", f"/connectors/{cid}/audit"),
    ]:
        assert connectors.client.request(method, f"/api/v1{path}", json={}).status_code == 401, (method, path)


def test_permissions_are_enforced_per_operation(connectors: Connectors) -> None:
    schema = connectors.schema()
    created = connectors.create(schema["id"])
    cid = created["id"]
    nobody = connectors.user_with(name="nobody")
    reader = connectors.user_with("connector.read", name="reader")

    assert connectors.get("/connectors", headers=nobody).status_code == 403
    assert connectors.get(f"/connectors/{cid}", headers=nobody).status_code == 403
    assert connectors.post(f"/connectors/{cid}/test", headers=nobody).status_code == 403

    assert connectors.get("/connectors", headers=reader).status_code == 200
    assert connectors.get(f"/connectors/{cid}/health", headers=reader).status_code == 200
    assert connectors.post("/connectors", connectors.body(schema["id"], name="X", endpoint_path="/x"), headers=reader).status_code == 403
    assert connectors.patch(f"/connectors/{cid}", {"description": "x"}, headers=reader).status_code == 403
    assert connectors.patch(f"/connectors/{cid}/status", {"is_active": True}, headers=reader).status_code == 403
    assert connectors.post(f"/connectors/{cid}/test", headers=reader).status_code == 403
    assert connectors.delete(f"/connectors/{cid}", headers=reader).status_code == 403
    assert len(connectors.company.requests) == 0

    for key, call in [
        ("connector.create", lambda h: connectors.post("/connectors", connectors.body(schema["id"], name="Y", endpoint_path="/y"), headers=h)),
        ("connector.update", lambda h: connectors.patch(f"/connectors/{cid}", {"description": "z"}, headers=h)),
        ("connector.activate", lambda h: connectors.patch(f"/connectors/{cid}/status", {"is_active": True}, headers=h)),
        ("connector.test", lambda h: connectors.post(f"/connectors/{cid}/test", headers=h)),
        ("connector.delete", lambda h: connectors.delete(f"/connectors/{cid}", headers=h)),
    ]:
        holder = connectors.user_with(key, name=key.replace(".", "_"))
        assert call(holder).status_code in (200, 201, 204), key
        # Holding one connector permission grants no other.
        assert connectors.get("/connectors", headers=holder).status_code == 403


def test_no_public_endpoint_accepts_sql(connectors: Connectors) -> None:
    schema = connectors.client.get("/openapi.json").json()

    def properties(node: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    found |= set(value)
                found |= properties(value)
        elif isinstance(node, list):
            for item in node:
                found |= properties(item)
        return found

    request_bodies = [
        operation["requestBody"]
        for path, item in schema["paths"].items()
        if "/connectors" in path
        for operation in item.values()
        if isinstance(operation, dict) and "requestBody" in operation
    ]
    referenced = {
        ref.rsplit("/", 1)[-1]
        for body in request_bodies
        for ref in json.dumps(body).split('"$ref": "')[1:]
        for ref in [ref.split('"', 1)[0]]
    }
    fields: set[str] = set()
    for name in referenced:
        fields |= properties(schema["components"]["schemas"][name])
    assert fields and not {"sql", "exact_sql", "query", "statement"} & fields


# --- the internal execution boundary (no route exposes it) -----------------------------------------------------------------


def _service_call(connectors: Connectors, call: Any) -> Any:
    from app.api.deps import get_connector_service
    from app.core.config import get_settings  # noqa: F401

    app = connectors.client.app

    class _Request:
        def __init__(self) -> None:
            self.app = app

    async def run() -> Any:
        async with app.state.database.session() as session:  # type: ignore[attr-defined]
            service = get_connector_service(_Request(), session, app.state.settings)  # type: ignore[arg-type]
            return await call(service)

    return connectors.api.run(run)


def test_service_execute_accepts_only_sealed_requests_and_audits(connectors: Connectors) -> None:
    import uuid as _uuid

    from app.connectors.errors import TrustedRequestRejectedError

    schema = connectors.schema()
    created = connectors.create(schema["id"])
    authority = connectors.client.app.state.execution_authority  # type: ignore[attr-defined]
    request = authority.issue_connector_test(
        connector_id=_uuid.UUID(created["id"]),
        requesting_user_id=connectors.admin_id,
        schema_source_id=_uuid.UUID(schema["id"]),
        schema_version=schema["schema_version"],
    )
    result = _service_call(connectors, lambda service: service.execute(request))
    assert result.rows == [{"opspilot_connection_test": 1}]
    executed = connectors.audit(created["id"])[-1]
    assert executed.operation_type == "executed" and executed.status == "success"
    assert executed.schema_version == schema["schema_version"] and executed.sql_length == len(TEST_SQL)
    assert executed.requesting_user_id == connectors.admin_id

    before = len(connectors.company.requests)
    for forged in ("SELECT * FROM orders", request.model_copy(update={"exact_sql": "SELECT * FROM salaries"})):
        with pytest.raises(TrustedRequestRejectedError):
            _service_call(connectors, lambda service, forged=forged: service.execute(forged))
    assert len(connectors.company.requests) == before
    rejected = [e for e in connectors.audit() if e.status == "rejected"]
    assert len(rejected) == 1 and rejected[0].error_category == "validation_rejected"
    assert "salaries" not in json.dumps([dict(e._mapping) for e in connectors.audit()], default=str)

    # Validated against an older schema version: refused.
    connectors.create_table(schema["id"], "orders", [{"column_name": "id", "data_type": "integer"}])
    with pytest.raises(TrustedRequestRejectedError):
        _service_call(connectors, lambda service: service.execute(request))


def test_key_rotation_reencrypts_with_primary_key(connectors_factory: Any) -> None:
    from tests.connectors.conftest import ENCRYPTION_KEY, OTHER_KEY

    old: Connectors = connectors_factory()
    created = old.create(custom_headers=[{"name": "X-Sig", "value": "header-secret", "secret": True}])
    before = old.stored(created["id"])
    rotated: Connectors = connectors_factory(
        old, connector_encryption_key=OTHER_KEY, connector_encryption_previous_keys=ENCRYPTION_KEY
    )
    changed = _service_call(rotated, lambda service: service.rotate_encryption_keys())
    assert changed == 1
    after = rotated.stored(created["id"])
    assert after.encrypted_credentials != before.encrypted_credentials
    assert after.custom_headers[0]["encrypted_value"] != before.custom_headers[0]["encrypted_value"]
    # The new key alone now works; the old key alone no longer does.
    only_new: Connectors = connectors_factory(old, connector_encryption_key=OTHER_KEY)
    assert only_new.test(created["id"], headers=only_new.admin)["status"] == "passed"
    assert only_new.company.last.headers["X-Sig"] == "header-secret"
    only_old: Connectors = connectors_factory(old)
    assert only_old.test(created["id"], headers=only_old.admin)["error_category"] == "encryption_unavailable"
    assert _service_call(only_new, lambda service: service.rotate_encryption_keys()) == 0
