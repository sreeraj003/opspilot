"""AI authorization context and permission caching (real database and engine)."""

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import event

from app.authorization.cache import AuthorizationCache
from app.authorization.engine import AuthorizationEngine
from app.models.user import User
from tests.rbac.conftest import SALES_VIEW, Rbac

pytestmark = pytest.mark.postgres

SECRET_FIELDS = ("password", "password_hash", "token", "session", "refresh", "cookie", "secret")


def test_ai_context_contains_the_effective_permissions(rbac: Rbac) -> None:
    user_id, headers = rbac.user()
    sales = rbac.create_permission("sales.karnataka.view")
    reports = rbac.create_permission(
        "executive_reports.view", {"allow_export": True}, resource_type="executive_reports"
    )
    rbac.assign_role(user_id, rbac.role_with("regional_manager", sales, reports))

    context = rbac.ai_context(user_id)

    assert context["user_id"] == str(user_id)
    assert context["roles"] == [{"key": "regional_manager", "name": "Regional Manager"}]
    assert context["permission_keys"] == ["executive_reports.view", "sales.karnataka.view"]
    assert context["allowed_resources"] == ["database_data", "executive_reports"]
    assert {"resource_type": "database_data", "action": "view"} in context["allowed_actions"]
    assert context["allowed_tables"] == ["sales"]
    assert "sales.amount" in context["allowed_columns"] and "sales.customer_email" not in context["allowed_columns"]
    table = context["data_access"][0]["tables"][0]
    assert table["row_filter"]["value"] == "Karnataka"
    assert table["operations"] == ["aggregate", "read"]
    assert table["allow_export"] is False and context["sql_view_allowed"] is False
    assert context["export_allowed"] is True  # through the executive reports permission
    assert isinstance(context["policy_revision"], int) and context["policy_revision"] > 0
    assert context["is_authorization_token"] is False
    # The authenticated user can read their own context over HTTP.
    assert rbac.ok(rbac.get("/authorization/me/ai-context", headers))["permission_keys"] == context["permission_keys"]


def test_ai_context_contains_only_relevant_information(rbac: Rbac) -> None:
    delegate_id, _ = rbac.user()
    rbac.assign_role(delegate_id, rbac.role_id("administrator"))
    rbac.assign_role(delegate_id, rbac.role_with("analyst", rbac.create_permission("sales.view")))

    context = rbac.ai_context(delegate_id)

    # Administrative (system) permissions are not part of what the AI may query.
    assert context["permission_keys"] == ["sales.view"]
    assert "system" not in context["allowed_resources"]
    assert set(context) == {
        "user_id",
        "policy_revision",
        "generated_at",
        "roles",
        "permission_keys",
        "allowed_resources",
        "allowed_actions",
        "resources",
        "data_access",
        "allowed_tables",
        "allowed_columns",
        "export_allowed",
        "sql_view_allowed",
        "is_authorization_token",
        "notice",
    }


def test_ai_context_contains_no_credentials_or_session_data(rbac: Rbac) -> None:
    user_id, headers = rbac.user()
    rbac.assign_role(user_id, rbac.role_with("analyst", rbac.create_permission("sales.view")))
    token = headers["Authorization"].split()[1]
    serialized = json.dumps(rbac.ai_context(user_id)).lower()
    for field in SECRET_FIELDS:
        assert f'"{field}' not in serialized
    assert token.lower() not in serialized
    assert "example.com" not in serialized  # no email either: the user id is enough
    password_hash = rbac.api.sql("SELECT password_hash FROM users WHERE id = :u", u=user_id)[0][0]
    assert password_hash.lower() not in serialized


def test_ai_context_excludes_revoked_and_expired_permissions(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    kept = rbac.create_permission("sales.view")
    revoked = rbac.create_permission("profit.view", {"allowed_tables": ["profit"], "allowed_columns": ["*"]})
    expired = rbac.create_permission("expenses.view", {"allowed_tables": ["expenses"], "allowed_columns": ["*"]})
    rbac.assign_role(user_id, rbac.role_with("analyst", kept))
    rbac.ok(rbac.grant_direct(user_id, revoked), 201)
    rbac.ok(rbac.grant_direct(user_id, expired), 201)
    assert rbac.ai_context(user_id)["permission_keys"] == ["expenses.view", "profit.view", "sales.view"]

    assert rbac.delete(f"/users/{user_id}/permissions/{revoked}").status_code == 204
    rbac.expire("user_permissions", user_id=user_id, permission_id=expired)
    rbac.client.app.state.authorization_cache.clear()  # type: ignore[attr-defined]  (raw SQL above skips the revision)

    context = rbac.ai_context(user_id)
    assert context["permission_keys"] == ["sales.view"]
    assert context["allowed_tables"] == ["sales"]


def test_ai_context_reflects_permission_updates(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    permission = rbac.create_permission("sales.view")
    rbac.assign_role(user_id, rbac.role_with("analyst", permission))
    before = rbac.ai_context(user_id)

    rbac.ok(rbac.patch(f"/permissions/{permission}", {"policy_definition": {**SALES_VIEW, "allow_sql_view": True}}))
    after = rbac.ai_context(user_id)

    assert after["policy_revision"] > before["policy_revision"]
    assert before["sql_view_allowed"] is False and after["sql_view_allowed"] is True


# --- caching ---------------------------------------------------------------------------------------


class _QueryCounter:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def __call__(self, conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        self.statements.append(statement)


def _engine_run(rbac: Rbac, fn: Any, cache: AuthorizationCache | None) -> tuple[Any, list[str]]:
    counter = _QueryCounter()
    sync_engine = rbac.client.app.state.database.engine.sync_engine  # type: ignore[attr-defined]

    async def run() -> Any:
        async with rbac.client.app.state.database.session() as session:  # type: ignore[attr-defined]
            event.listen(sync_engine, "before_cursor_execute", counter)
            try:
                return await fn(AuthorizationEngine(session, cache), session)
            finally:
                event.remove(sync_engine, "before_cursor_execute", counter)

    return rbac.api.run(run), counter.statements


def test_cache_hit_needs_only_the_revision_lookup(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    rbac.assign_role(user_id, rbac.role_with("analyst", rbac.create_permission("sales.view")))
    cache = AuthorizationCache(16)

    async def effective(engine: AuthorizationEngine, session: Any) -> Any:
        user = await session.get(User, user_id)
        return await engine.effective(user)

    first, cold = _engine_run(rbac, effective, cache)
    second, warm = _engine_run(rbac, effective, cache)
    assert first.permission_keys == second.permission_keys == ["sales.view"]
    assert len(warm) == len(cold) - 3  # user row + revision only; the three grant queries are skipped
    assert any("authorization_state" in s for s in warm)
    assert cache.hits == 1


def test_bulk_resolution_does_not_query_per_user(rbac: Rbac) -> None:
    permission = rbac.create_permission("sales.view")
    roles = [rbac.role_with(f"team_{n}", permission) for n in range(3)]
    users = []
    for n in range(6):
        user_id, _ = rbac.user()
        rbac.assign_role(user_id, roles[n % 3])
        users.append(user_id)

    async def resolve(engine: AuthorizationEngine, _session: Any) -> Any:
        return await engine.resolve_many(users)

    resolved, statements = _engine_run(rbac, resolve, None)
    assert len(resolved) == 6
    assert all(r.grants[0].key == "sales.view" for r in resolved.values())
    assert len(statements) == 4  # revision + roles + role grants + direct grants, independent of user count


def test_every_change_bumps_the_revision(rbac: Rbac) -> None:
    def revision() -> int:
        return rbac.api.sql("SELECT revision FROM authorization_state")[0][0]

    user_id, _ = rbac.user()
    steps = []
    start = revision()
    role = rbac.create_role("analyst")
    steps.append(revision())
    permission = rbac.create_permission("sales.view")
    steps.append(revision())
    rbac.grant_role_permissions(role, permission)
    steps.append(revision())
    rbac.assign_role(user_id, role)
    steps.append(revision())
    rbac.ok(rbac.grant_direct(user_id, rbac.create_permission("x.view", {}, resource_type="reports")), 201)
    steps.append(revision())
    rbac.ok(rbac.patch(f"/roles/{role}", {"is_active": False}))
    steps.append(revision())
    rbac.post(f"/users/{user_id}/deactivate")
    steps.append(revision())
    assert steps == sorted(steps) and len(set(steps)) == len(steps) and steps[0] > start


def test_cache_is_bounded() -> None:
    from app.authorization.engine import ResolvedAccess

    cache = AuthorizationCache(2)
    ids = [uuid.uuid4() for _ in range(3)]
    for user_id in ids:
        cache.put(ResolvedAccess(user_id, 1, (), ()))
    assert len(cache) == 2
    assert cache.get(ids[0], 1) is None
    assert cache.get(ids[2], 1) is not None
    assert cache.get(ids[2], 2) is None  # another revision: never served
    disabled = AuthorizationCache(0)
    disabled.put(ResolvedAccess(ids[0], 1, (), ()))
    assert len(disabled) == 0
