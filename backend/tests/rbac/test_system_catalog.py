"""Built-in system permissions: code, migration seed and repair stay in step."""

import pytest

from app.authorization.system_catalog import ensure_system_rbac
from app.core.authorization import ADMINISTRATOR_ONLY_PERMISSIONS, DELEGABLE_SYSTEM_PERMISSIONS, Permission
from tests.rbac.conftest import Rbac

pytestmark = pytest.mark.postgres


def _sync(rbac: Rbac) -> int:
    async def run() -> int:
        async with rbac.client.app.state.database.session() as session:  # type: ignore[attr-defined]
            return await ensure_system_rbac(session)

    return rbac.api.run(run)


def test_every_route_permission_is_classified() -> None:
    assert DELEGABLE_SYSTEM_PERMISSIONS | ADMINISTRATOR_ONLY_PERMISSIONS == {p.value for p in Permission}
    assert not DELEGABLE_SYSTEM_PERMISSIONS & ADMINISTRATOR_ONLY_PERMISSIONS
    # Anything that changes users, settings, roles, permissions or assignments is administrator-only.
    assert {"users:manage", "settings:manage", "roles:manage", "permissions:manage", "access:assign"} <= ADMINISTRATOR_ONLY_PERMISSIONS


def test_seeded_rows_match_the_code(rbac: Rbac) -> None:
    assert _sync(rbac) == 0


def test_sync_repairs_a_damaged_installation(rbac: Rbac) -> None:
    rbac.api.sql(
        "DELETE FROM role_permissions WHERE permission_id = (SELECT id FROM permissions WHERE key = 'roles:read')"
    )
    rbac.api.sql("UPDATE roles SET is_active = false WHERE key = 'administrator'")
    assert _sync(rbac) == 2
    assert _sync(rbac) == 0
    admin = next(r for r in rbac.ok(rbac.get("/roles?search=administrator"))["items"])
    assert admin["is_active"] and admin["permission_count"] == len(Permission)
