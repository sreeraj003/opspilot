"""Fixtures for Module 04 tests against the real database, models and engine.

Reuses the Module 03 ``Api`` harness (real app, real PostgreSQL, real login).
``Rbac`` adds helpers that go through the public HTTP API as an administrator,
plus direct access to ``AuthorizationEngine`` for engine-level assertions.
"""

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import Response

from app.authorization.engine import AuthorizationDecision, AuthorizationEngine, AuthorizationRequest
from app.repositories.user_repository import UserRepository
from tests.auth.conftest import ADMIN_EMAIL, Api, api, api_factory  # noqa: F401  (fixtures)

pytestmark = pytest.mark.postgres

SALES_VIEW: dict[str, Any] = {
    "allowed_tables": ["sales"],
    "allowed_columns": ["product_id", "amount", "sale_date", "region"],
    "row_filters": [{"column": "region", "operator": "equals", "value": "Karnataka"}],
    "allow_export": False,
    "allow_sql_view": False,
}


class Rbac:
    def __init__(self, api: Api) -> None:
        self.api = api
        self.client = api.client
        self.admin_id = api.create_user(ADMIN_EMAIL, full_name="Installation Admin", superuser=True)
        self.admin = api.bearer(api.token_for(ADMIN_EMAIL))
        api.clear_cookies()
        self._users = 0

    # --- users -----------------------------------------------------------------------------

    def user(self, name: str = "member") -> tuple[uuid.UUID, dict[str, str]]:
        """A new ordinary user and their bearer header."""
        self._users += 1
        email = f"{name}{self._users}@example.com"
        user_id = self.api.create_user(email, full_name=name.title())
        headers = self.api.bearer(self.api.token_for(email))
        self.api.clear_cookies()
        return user_id, headers

    # --- HTTP helpers ------------------------------------------------------------------------

    def post(self, path: str, body: Any = None, headers: dict[str, str] | None = None) -> Response:
        return self.client.post(f"/api/v1{path}", json=body, headers=headers or self.admin)

    def patch(self, path: str, body: Any, headers: dict[str, str] | None = None) -> Response:
        return self.client.patch(f"/api/v1{path}", json=body, headers=headers or self.admin)

    def put(self, path: str, body: Any, headers: dict[str, str] | None = None) -> Response:
        return self.client.put(f"/api/v1{path}", json=body, headers=headers or self.admin)

    def get(self, path: str, headers: dict[str, str] | None = None) -> Response:
        return self.client.get(f"/api/v1{path}", headers=headers or self.admin)

    def delete(self, path: str, headers: dict[str, str] | None = None) -> Response:
        return self.client.delete(f"/api/v1{path}", headers=headers or self.admin)

    def ok(self, response: Response, status: int = 200) -> Any:
        assert response.status_code == status, response.text
        return response.json() if response.content else None

    # --- domain helpers -------------------------------------------------------------------------

    def create_role(self, key: str, name: str | None = None, **extra: Any) -> str:
        body = {"key": key, "name": name or key.replace("_", " ").title(), **extra}
        return self.ok(self.post("/roles", body), 201)["id"]

    def create_permission(
        self, key: str, policy: dict[str, Any] | None = None, *, resource_type: str = "database_data", action: str = "view", **extra: Any
    ) -> str:
        body = {
            "key": key,
            "name": extra.pop("name", key),
            "resource_type": resource_type,
            "action": action,
            "policy_definition": SALES_VIEW if policy is None else policy,
            **extra,
        }
        return self.ok(self.post("/permissions", body), 201)["id"]

    def system_permission_id(self, key: str) -> str:
        page = self.ok(self.get(f"/permissions?search={key}&resource_type=system"))
        return next(p["id"] for p in page["items"] if p["key"] == key)

    def role_id(self, key: str) -> str:
        page = self.ok(self.get(f"/roles?search={key}"))
        return next(r["id"] for r in page["items"] if r["key"] == key)

    def grant_role_permissions(self, role_id: str, *permission_ids: str) -> None:
        self.ok(self.post(f"/roles/{role_id}/permissions", {"permission_ids": list(permission_ids)}), 201)

    def assign_role(self, user_id: uuid.UUID | str, role_id: str, **extra: Any) -> None:
        self.ok(self.post(f"/users/{user_id}/roles", {"role_id": role_id, **extra}), 201)

    def grant_direct(self, user_id: uuid.UUID | str, permission_id: str, **extra: Any) -> Response:
        return self.post(f"/users/{user_id}/permissions", {"permission_id": permission_id, **extra})

    def role_with(self, key: str, *permission_ids: str) -> str:
        role_id = self.create_role(key)
        if permission_ids:
            self.grant_role_permissions(role_id, *permission_ids)
        return role_id

    def check(self, headers: dict[str, str] | None = None, **body: Any) -> dict[str, Any]:
        return self.ok(self.post("/authorization/check", body, headers=headers))

    # --- engine -----------------------------------------------------------------------------

    def authorize(self, user_id: uuid.UUID | str, *, fresh: bool = False, **fields: Any) -> AuthorizationDecision:
        request = AuthorizationRequest(user_id=uuid.UUID(str(user_id)), **fields)

        async def run() -> AuthorizationDecision:
            async with self.client.app.state.database.session() as session:  # type: ignore[attr-defined]
                engine = AuthorizationEngine(session, self.client.app.state.authorization_cache)  # type: ignore[attr-defined]
                return await engine.authorize(request, fresh=fresh)

        return self.api.run(run)

    def ai_context(self, user_id: uuid.UUID | str) -> dict[str, Any]:
        async def run() -> dict[str, Any]:
            async with self.client.app.state.database.session() as session:  # type: ignore[attr-defined]
                user = await UserRepository(session).get(uuid.UUID(str(user_id)))
                assert user is not None
                engine = AuthorizationEngine(session, self.client.app.state.authorization_cache)  # type: ignore[attr-defined]
                return await engine.ai_authorization_context(user)

        return self.api.run(run)

    def expire(self, table: str, **where: Any) -> None:
        """Move an assignment into the past so that it has expired."""
        conditions = " AND ".join(f"{column} = :{column}" for column in where)
        self.api.sql(
            f"UPDATE {table} SET assigned_at = now() - interval '2 days', expires_at = now() - interval '1 day' "
            f"WHERE {conditions} AND revoked_at IS NULL",
            **where,
        )


@pytest.fixture
def rbac(api: Api) -> Rbac:  # noqa: F811
    return Rbac(api)


@pytest.fixture
def make_rbac(api_factory: Callable[..., Api]) -> Callable[..., Rbac]:  # noqa: F811
    return lambda **overrides: Rbac(api_factory(**overrides))
