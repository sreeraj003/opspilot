"""The migrated database must match the RBAC catalog in code."""

import re
from pathlib import Path

from alembic import command
from sqlalchemy import select

from app.database.session import Database
from app.models import Permission as PermissionRow
from app.models import Role
from app.security.rbac import PERMISSION_DESCRIPTIONS, ROLES, Permission
from tests.conftest import BACKEND_DIR, alembic_config


def test_seeded_permissions_match_catalog(database: Database) -> None:
    with database.session() as session:
        rows = {p.code: p.description for p in session.scalars(select(PermissionRow))}
    assert rows == {str(p): text for p, text in PERMISSION_DESCRIPTIONS.items()}
    assert set(PERMISSION_DESCRIPTIONS) == set(Permission)


def test_seeded_roles_match_catalog(database: Database) -> None:
    with database.session() as session:
        rows = {r.code: (r.name, r.permission_codes) for r in session.scalars(select(Role))}
    assert rows == {role.code: (role.name, frozenset(str(p) for p in role.permissions)) for role in ROLES}


def test_only_super_admin_is_platform_wide() -> None:
    assert [r.code for r in ROLES if Permission.PLATFORM_ADMIN in r.permissions] == ["super_admin"]


def test_application_code_checks_permissions_not_role_names() -> None:
    role_codes = "|".join(re.escape(role.code) for role in ROLES)
    pattern = re.compile(rf"[\"']({role_codes})[\"']")
    allowed = {BACKEND_DIR / "app" / "security" / "rbac.py"}
    offenders = [
        str(path.relative_to(BACKEND_DIR))
        for path in (BACKEND_DIR / "app").rglob("*.py")
        if path not in allowed and pattern.search(path.read_text())
    ]
    assert offenders == []


def test_migrations_match_models(database_url: str) -> None:
    # Fails if a model changed without a corresponding migration.
    command.check(alembic_config(database_url))


def test_migration_files_exist() -> None:
    assert list(Path(BACKEND_DIR / "migrations" / "versions").glob("*_0001_*.py"))
