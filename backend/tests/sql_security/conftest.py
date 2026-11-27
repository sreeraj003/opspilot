"""Fixtures for Module 11 API tests: the real app, PostgreSQL, permission engine, schema registry,
SQL generation records and the Module 07 connector with a mocked company API.

No test reaches a real database or company API. ``FakeCompanyApi`` (an
``httpx.MockTransport`` handler) records every request the connector would
send, so tests can prove that unapproved SQL never reaches HTTPX and that
approved SQL reaches it exactly once.

Generations are stored directly (``stored_generation``) with the SQL a test
wants, because the guard must not trust Module 10: anything Module 10 might
store, including hostile SQL, has to be handled. ``generated`` uses the real
Module 10 flow end to end.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import Response

from app.ai.sql_security.services.execution_service import SQLExecutionGuard
from app.ai.sql_security.services.inspector import sql_sha256
from app.ai.sql_security.services.validation_service import SQLSecurityValidationService
from app.ai.understanding.services.terminology_service import TerminologyService
from app.api.deps import get_schema_registry_service
from app.authorization.engine import AuthorizationEngine
from app.connectors.adapters.execution import ConnectorExecutionAdapter
from app.models.user import User
from app.services.connector_service import ConnectorService
from tests.ai.conftest import API_KEY, MODEL, FakeProvider, completion, provider_api  # noqa: F401  (fixtures)
from tests.auth.conftest import Api, api_factory  # noqa: F401  (fixtures)
from tests.connectors.conftest import COMPANY_HOST, DEFAULT_CONNECTOR_SETTINGS, FakeCompanyApi, FakeDns
from tests.sql_generation.conftest import RANKING_SQL, VIEW_SQL_POLICY, SQLGeneration
from tests.understanding.conftest import SALES_POLICY

pytestmark = pytest.mark.postgres

GOOD_SQL = RANKING_SQL["postgresql"]
SOUTH_POLICY: dict[str, Any] = {
    "allowed_tables": ["orders", "customers", "products"],
    "allowed_columns": ["orders.*", "customers.id", "customers.name", "customers.region", "products.*"],
    "allowed_operations": ["aggregate", "read"],
    "row_filters": [
        {"column": "customers.region", "operator": "equals", "value": "South"},
        {"column": "orders.status", "operator": "not_equals", "value": "deleted"},
    ],
    "allow_export": False,
    "allow_sql_view": True,
}


class Clock:
    def __init__(self) -> None:
        self.offset = timedelta()

    def __call__(self) -> datetime:
        return datetime.now(UTC) + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += timedelta(seconds=seconds)


class Guard(SQLGeneration):
    def __init__(self, api: Api, provider: FakeProvider, company: FakeCompanyApi, clock: Clock) -> None:
        super().__init__(api, provider)
        self.company = company
        self.clock = clock
        self.connector_id = self.create_connector()

    # --- setup ----------------------------------------------------------------------------------

    def create_connector(self, schema_id: str | None = None, name: str = "Company SQL API", path: str = "/api/sql/execute") -> str:
        body = {
            "name": name, "schema_source_id": schema_id or self.schema_id, "base_url": f"https://{COMPANY_HOST}",
            "endpoint_path": path, "authentication_type": "api_key", "credential": "sk_live_secret_value_999",
        }
        connector = self.ok(self.post("/connectors", body), 201)
        self.ok(self.patch(f"/connectors/{connector['id']}/status", {"is_active": True}))
        return str(connector["id"])

    def member(self, *system_keys: str, policy: dict[str, Any] | None = VIEW_SQL_POLICY,
               key: str = "sales.view") -> tuple[uuid.UUID, dict[str, str]]:
        """A user with SQL generation and validation (or ``system_keys``) and one data permission."""
        user_id, headers = self.user("analyst")
        keys = system_keys or ("ai.sql.generate", "ai.sql.validate")
        permission_ids = [self.system_permission_id(k) for k in keys]
        if policy is not None:
            permission_ids.append(self.create_permission(f"{key}.{self._users}", policy))
        self.assign_role(user_id, self.role_with(f"analyst_{self._users}", *permission_ids))
        return user_id, headers

    def schema_version(self) -> int:
        return int(self.ok(self.get(f"/schemas/{self.schema_id}/version"))["schema_version"])

    def stored_generation(self, user_id: uuid.UUID, sql: str | None, *, dialect: str = "postgres",
                          status: str = "generated", schema_version: int | None = None,
                          sha: str | None = None, schema_id: str | None = None) -> str:
        generation_id = uuid.uuid4()
        self.api.sql(
            "INSERT INTO sql_generations (id, user_id, schema_source_id, schema_version, intent_id, dialect, "
            "generated_sql, sql_sha256, generation_status, validation_status, prompt_version, generation_attempts, "
            "latency_ms, policy_revision) VALUES (:id, :user_id, :schema_id, :version, :intent, :dialect, :sql, :sha, "
            ":status, 'passed', 'sql-generation-v1', 1, 5, 1)",
            id=generation_id, user_id=user_id, schema_id=schema_id or self.schema_id,
            version=schema_version or self.schema_version(), intent=uuid.uuid4(), dialect=dialect, sql=sql,
            sha=(sha if sha is not None else (sql_sha256(sql) if sql else None)), status=status,
        )
        return str(generation_id)

    # --- API --------------------------------------------------------------------------------------

    def validate(self, generation_id: str, headers: dict[str, str], **body: Any) -> Response:
        return self.post(f"/ai/sql/generations/{generation_id}/validate", body or None, headers=headers)

    def validated(self, generation_id: str, headers: dict[str, str], **body: Any) -> dict[str, Any]:
        return self.ok(self.validate(generation_id, headers, **body))

    def check(self, sql: str, policy: dict[str, Any] | None = VIEW_SQL_POLICY, **body: Any) -> dict[str, Any]:
        user_id, headers = self.member(policy=policy)
        return self.validated(self.stored_generation(user_id, sql), headers, **body)

    def approval(self, approval_id: str, headers: dict[str, str]) -> Response:
        return self.get(f"/ai/sql/approvals/{approval_id}", headers=headers)

    def revoke(self, approval_id: str, headers: dict[str, str]) -> Response:
        return self.post(f"/ai/sql/approvals/{approval_id}/revoke", {}, headers=headers)

    def approvals(self) -> list[Any]:
        return self.api.sql("SELECT * FROM sql_execution_approvals ORDER BY approved_at")

    def security_events(self, event_type: str | None = None) -> list[Any]:
        if event_type is None:
            return self.api.sql("SELECT * FROM sql_security_events ORDER BY created_at")
        return self.api.sql("SELECT * FROM sql_security_events WHERE event_type = :t ORDER BY created_at", t=event_type)

    # --- the internal execution boundary (no API route exposes it) ----------------------------------

    def _services(self, session: Any) -> tuple[SQLSecurityValidationService, SQLExecutionGuard, ConnectorService]:
        app = self.client.app
        state = app.state  # type: ignore[attr-defined]
        settings = state.settings
        terminology = TerminologyService(session, get_schema_registry_service(session, settings))
        validation = SQLSecurityValidationService(
            session, settings=settings, terminology=terminology, engine=AuthorizationEngine(session, None), clock=self.clock
        )
        adapter = ConnectorExecutionAdapter(state.connector_http, state.execution_authority)
        connectors = ConnectorService(session, settings=settings, cipher=state.connector_cipher, policy=state.connector_policy,
                                      adapter=adapter, authority=state.execution_authority)
        return validation, SQLExecutionGuard(validation, state.execution_authority), connectors

    def execute(self, user_id: uuid.UUID, approval_id: str, *, connector_id: str | None = None,
                exact_sql: str | None = None) -> Any:
        async def run() -> Any:
            async with self.client.app.state.database.session() as session:  # type: ignore[attr-defined]
                _, guard, connectors = self._services(session)
                user = await session.get(User, user_id)
                assert user is not None
                return await guard.execute(user, uuid.UUID(approval_id), connectors,
                                           connector_id=uuid.UUID(connector_id or self.connector_id), exact_sql=exact_sql)

        return self.api.run(run)

    def authorize_execution(self, user_id: uuid.UUID, approval_id: str, *, connector_id: str | None = None) -> Any:
        async def run() -> Any:
            async with self.client.app.state.database.session() as session:  # type: ignore[attr-defined]
                _, guard, _ = self._services(session)
                user = await session.get(User, user_id)
                assert user is not None
                return await guard.authorize_execution(user, uuid.UUID(approval_id),
                                                       connector_id=uuid.UUID(connector_id or self.connector_id))

        return self.api.run(run)

    def connector_execute(self, request: Any) -> Any:
        async def run() -> Any:
            async with self.client.app.state.database.session() as session:  # type: ignore[attr-defined]
                _, _, connectors = self._services(session)
                return await connectors.execute(request)

        return self.api.run(run)

    def sql_requests(self) -> list[httpx.Request]:
        """Requests the company API received that carried SQL (not the connector test)."""
        return [r for r in self.company.requests if "sql" in str(r.url.params) and "opspilot_connection_test" not in str(r.url)]


def _prepare(provider: FakeProvider, company: FakeCompanyApi, dns: FakeDns, clock: Clock) -> Callable[[FastAPI], None]:
    def prepare(app: FastAPI) -> None:
        app.state.ai_transport = httpx.MockTransport(provider)
        app.state.connector_transport = httpx.MockTransport(company)
        app.state.connector_policy.resolver = dns
        app.state.sql_security_clock = clock

    return prepare


@pytest.fixture
def guard_factory(api_factory: Callable[..., Api], provider_api: FakeProvider) -> Callable[..., Guard]:  # noqa: F811
    def factory(**overrides: Any) -> Guard:
        company, dns, clock = FakeCompanyApi(), FakeDns(), Clock()
        values = {
            "nvidia_api_key": API_KEY, "nvidia_model": MODEL, "ai_max_retries": 0, "ai_retry_max_wait_seconds": 0,
            **DEFAULT_CONNECTOR_SETTINGS, **overrides,
        }
        return Guard(api_factory(prepare=_prepare(provider_api, company, dns, clock), **values), provider_api, company, clock)

    return factory


@pytest.fixture
def guard(guard_factory: Callable[..., Guard]) -> Guard:
    return guard_factory()


__all__ = ["GOOD_SQL", "SALES_POLICY", "SOUTH_POLICY", "VIEW_SQL_POLICY", "Guard", "completion"]
