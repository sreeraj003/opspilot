"""Fixtures for Module 07 tests: the real app and PostgreSQL, with a mocked company API.

No test talks to a real external API. ``FakeCompanyApi`` is an
``httpx.MockTransport`` handler installed as the connector client's transport,
and ``FakeDns`` replaces name resolution, so the SSRF checks, IP pinning,
encoding, headers and response handling all run for real against canned answers.
"""

import base64
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from tests.auth.conftest import Api, api_factory  # noqa: F401  (fixtures)
from tests.schema_registry.conftest import Registry

pytestmark = pytest.mark.postgres

ENCRYPTION_KEY = base64.urlsafe_b64encode(b"k" * 32).decode()
OTHER_KEY = base64.urlsafe_b64encode(b"o" * 32).decode()
COMPANY_HOST = "company-backend.example.com"
COMPANY_IP = "93.184.216.34"
API_KEY = "sk_live_super_secret_value_123"
BEARER = "bearer-token-very-secret-456"


def envelope(rows: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    return {"success": True, "data": rows if rows is not None else [{"opspilot_connection_test": 1}],
            "metadata": {"execution_time_ms": 3}, **extra}


@dataclass
class FakeCompanyApi:
    """Records every request and answers with ``responder`` (default: a successful envelope)."""

    requests: list[httpx.Request] = field(default_factory=list)
    responder: Callable[[httpx.Request], httpx.Response] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.responder is not None:
            return self.responder(request)
        return httpx.Response(200, json=envelope())

    def reply(self, status: int = 200, body: Any = None, *, content_type: str = "application/json", raw: bytes | None = None,
              headers: dict[str, str] | None = None) -> None:
        def respond(_: httpx.Request) -> httpx.Response:
            content = raw if raw is not None else json.dumps(envelope() if body is None else body).encode()
            return httpx.Response(status, content=content, headers={"content-type": content_type, **(headers or {})})

        self.responder = respond

    def fail(self, exc: Exception) -> None:
        def respond(_: httpx.Request) -> httpx.Response:
            raise exc

        self.responder = respond

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


@dataclass
class FakeDns:
    answers: dict[str, Sequence[str]] = field(default_factory=lambda: {COMPANY_HOST: [COMPANY_IP]})
    lookups: list[str] = field(default_factory=list)

    async def __call__(self, host: str, port: int) -> Sequence[str]:
        self.lookups.append(host)
        if host not in self.answers:
            raise OSError("Name or service not known")
        return self.answers[host]


class Connectors(Registry):
    def __init__(self, api: Api, company: FakeCompanyApi, dns: FakeDns, same_admin_as: "Connectors | None" = None) -> None:
        if same_admin_as is None:
            super().__init__(api)
        else:  # a second backend process over the same database: reuse the administrator's token
            self.api, self.client = api, api.client
            self.admin_id, self.admin, self._users = same_admin_as.admin_id, same_admin_as.admin, 100
        self.company = company
        self.dns = dns
        self._schemas = 0

    def schema(self, name: str | None = None) -> dict[str, Any]:
        self._schemas += 1
        return self.create_schema(name or f"Sales DB {self._schemas}")

    def body(self, schema_id: str, **extra: Any) -> dict[str, Any]:
        return {
            "name": "Company SQL API",
            "schema_source_id": schema_id,
            "base_url": f"https://{COMPANY_HOST}",
            "endpoint_path": "/api/sql/execute",
            "authentication_type": "api_key",
            "credential": API_KEY,
            **extra,
        }

    def create(self, schema_id: str | None = None, headers: dict[str, str] | None = None, **extra: Any) -> dict[str, Any]:
        schema_id = schema_id or self.schema()["id"]
        return self.ok(self.post("/connectors", self.body(schema_id, **extra), headers=headers), 201)

    def test(self, connector_id: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        return self.ok(self.post(f"/connectors/{connector_id}/test", headers=headers))

    def stored(self, connector_id: str) -> Any:
        return self.api.sql("SELECT * FROM external_api_connectors WHERE id = :id", id=connector_id)[0]

    def audit(self, connector_id: str | None = None) -> list[Any]:
        if connector_id is None:
            return self.api.sql("SELECT * FROM connector_audit_events ORDER BY created_at")
        return self.api.sql(
            "SELECT * FROM connector_audit_events WHERE connector_id = :id ORDER BY created_at", id=connector_id
        )


def _prepare(company: FakeCompanyApi, dns: FakeDns) -> Callable[[FastAPI], None]:
    def prepare(app: FastAPI) -> None:
        app.state.connector_transport = httpx.MockTransport(company)
        app.state.connector_policy.resolver = dns

    return prepare


DEFAULT_CONNECTOR_SETTINGS: dict[str, Any] = {
    "connector_encryption_key": ENCRYPTION_KEY,
    "connector_allowed_hosts": [COMPANY_HOST, "*.corp.example.com", "10.1.2.3"],
}


@pytest.fixture
def company() -> FakeCompanyApi:
    return FakeCompanyApi()


@pytest.fixture
def dns() -> FakeDns:
    return FakeDns()


@pytest.fixture
def connectors_factory(
    api_factory: Callable[..., Api], company: FakeCompanyApi, dns: FakeDns  # noqa: F811
) -> Callable[..., Connectors]:
    def factory(same_admin_as: Connectors | None = None, **overrides: Any) -> Connectors:
        values = {**DEFAULT_CONNECTOR_SETTINGS, **overrides}
        return Connectors(api_factory(prepare=_prepare(company, dns), **values), company, dns, same_admin_as)

    return factory


@pytest.fixture
def connectors(connectors_factory: Callable[..., Connectors]) -> Connectors:
    return connectors_factory()
