"""Alembic configuration and migrations.

The upgrade/downgrade cycle runs in a brand-new database created next to the
test database (``<test db>_migrations``) and dropped afterwards, so it starts
truly empty and cannot disturb other tests.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError

from app.database.base import Base
from tests.conftest import BACKEND_DIR, alembic_config

MIGRATIONS_DIR = BACKEND_DIR / "migrations" / "versions"
AUTH_TABLES = {"users", "user_sessions", "retired_refresh_tokens", "password_reset_tokens", "auth_events"}
RBAC_TABLES = {
    "roles",
    "permissions",
    "role_permissions",
    "user_roles",
    "user_permissions",
    "rbac_audit_events",
    "authorization_state",
}
SCHEMA_REGISTRY_TABLES = {
    "schema_sources",
    "schema_tables",
    "schema_columns",
    "schema_relationships",
    "schema_change_events",
}
CONNECTOR_TABLES = {"external_api_connectors", "connector_audit_events"}
AI_TABLES = {"ai_usage_records"}


def test_alembic_ini_contains_no_database_url() -> None:
    assert "sqlalchemy.url" not in (BACKEND_DIR / "alembic.ini").read_text()


def test_single_linear_history_starting_with_application_settings() -> None:
    script = ScriptDirectory.from_config(alembic_config("postgresql://unused@localhost/unused_test"))

    assert script.get_heads() == ["0006"]
    revisions = list(script.walk_revisions())
    assert [r.revision for r in revisions] == ["0006", "0005", "0004", "0003", "0002", "0001"]
    assert revisions[0].down_revision == "0005"
    assert revisions[1].down_revision == "0004"
    assert revisions[2].down_revision == "0003"
    assert revisions[3].down_revision == "0002"
    assert revisions[4].down_revision == "0001"
    assert revisions[5].down_revision is None


def test_migration_files_follow_the_naming_scheme() -> None:
    files = sorted(Path(MIGRATIONS_DIR).glob("*.py"))

    assert [f.name for f in files] == [
        "2026_11_25_0001_create_application_settings.py",
        "2026_11_25_0002_create_authentication_tables.py",
        "2026_11_25_0003_create_rbac_tables.py",
        "2026_11_26_0004_create_schema_registry_tables.py",
        "2026_11_26_0005_create_external_api_connector_tables.py",
        "2026_11_26_0006_create_ai_usage_records.py",
    ]


@pytest.fixture
def empty_database(postgres_url: str) -> Iterator[str]:
    url = make_url(postgres_url)
    name = f"{url.database}_migrations_{uuid.uuid4().hex[:8]}"
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    except ProgrammingError:
        admin.dispose()
        pytest.skip("The test database user cannot CREATE DATABASE; migration cycle test skipped")
    try:
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


@pytest.mark.postgres
def test_upgrade_downgrade_and_upgrade_again(empty_database: str) -> None:
    config = alembic_config(empty_database)
    engine = create_engine(empty_database)
    try:
        assert _tables(engine) == set()

        command.upgrade(config, "head")
        assert _tables(engine) == {
            "alembic_version", "application_settings", *AUTH_TABLES, *RBAC_TABLES, *SCHEMA_REGISTRY_TABLES,
            *CONNECTOR_TABLES, *AI_TABLES,
        }
        assert _current_revision(engine) == "0006"
        _assert_application_settings_structure(engine)
        _assert_auth_structure(engine)
        _assert_rbac_structure_and_seed(engine)
        _assert_schema_registry_structure(engine)
        _assert_connector_structure(engine)
        _assert_ai_structure(engine)

        command.downgrade(config, "0005")
        assert _tables(engine) == {
            "alembic_version", "application_settings", *AUTH_TABLES, *RBAC_TABLES, *SCHEMA_REGISTRY_TABLES, *CONNECTOR_TABLES
        }
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM permissions WHERE key LIKE 'ai.%'")).scalar_one() == 0
            assert connection.execute(text("SELECT revision FROM authorization_state")).scalar_one() == 5

        command.downgrade(config, "0004")
        assert _tables(engine) == {
            "alembic_version", "application_settings", *AUTH_TABLES, *RBAC_TABLES, *SCHEMA_REGISTRY_TABLES
        }
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM permissions WHERE key LIKE 'connector.%'")).scalar_one() == 0
            assert connection.execute(text("SELECT revision FROM authorization_state")).scalar_one() == 6

        command.downgrade(config, "0003")
        assert _tables(engine) == {"alembic_version", "application_settings", *AUTH_TABLES, *RBAC_TABLES}
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM permissions WHERE key LIKE 'schema.%'")).scalar_one() == 0

        command.downgrade(config, "0002")
        assert _tables(engine) == {"alembic_version", "application_settings", *AUTH_TABLES}

        command.downgrade(config, "0001")
        assert _tables(engine) == {"alembic_version", "application_settings"}

        command.downgrade(config, "base")
        assert _tables(engine) == {"alembic_version"}
        assert _current_revision(engine) is None

        command.upgrade(config, "head")
        assert _current_revision(engine) == "0006"
        _assert_application_settings_structure(engine)
        _assert_auth_structure(engine)
        _assert_rbac_structure_and_seed(engine)
        _assert_schema_registry_structure(engine)
        _assert_connector_structure(engine)
        _assert_ai_structure(engine)
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_migrations_match_the_models(migrated_database: str) -> None:
    """Autogenerate finds nothing to do: no model change is missing a migration."""
    engine = create_engine(migrated_database)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "compare_type": True,
                    "compare_server_default": True,
                    # Ignore the test-only tables other tests create in this database.
                    "include_name": lambda name, type_, _: type_ != "table" or not name.startswith("test_"),
                },
            )
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()


def _assert_application_settings_structure(engine: Engine) -> None:
    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("application_settings")}

    assert list(columns) == ["id", "key", "value", "description", "created_at", "updated_at"]
    assert str(columns["id"]["type"]) == "UUID"
    assert str(columns["key"]["type"]) == "VARCHAR(128)"
    assert str(columns["value"]["type"]) == "JSONB"
    assert str(columns["description"]["type"]) == "TEXT"
    assert str(columns["created_at"]["type"]) == "TIMESTAMP"  # reflected generically; timezone checked below
    assert columns["created_at"]["type"].timezone is True  # type: ignore[attr-defined]
    assert columns["updated_at"]["type"].timezone is True  # type: ignore[attr-defined]
    assert {name for name, column in columns.items() if column["nullable"]} == {"description"}
    assert "gen_random_uuid()" in str(columns["id"]["default"])
    assert "now()" in str(columns["created_at"]["default"])

    assert inspector.get_pk_constraint("application_settings")["name"] == "pk_application_settings"
    assert [(u["name"], u["column_names"]) for u in inspector.get_unique_constraints("application_settings")] == [
        ("uq_application_settings_key", ["key"])
    ]
    assert {c["name"] for c in inspector.get_check_constraints("application_settings")} == {
        "ck_application_settings_key_format",
        "ck_application_settings_description_length",
        "ck_application_settings_value_size",
        "ck_application_settings_value_not_null",
    }
    assert inspector.get_foreign_keys("application_settings") == []


def _assert_auth_structure(engine: Engine) -> None:
    inspector = inspect(engine)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert {"email", "password_hash", "is_active", "is_superuser", "failed_login_attempts", "locked_until"} <= user_columns
    assert "password" not in user_columns
    indexes = {index["name"]: index for index in inspector.get_indexes("users")}
    assert indexes["ix_users_email_live"]["unique"]
    assert indexes["ix_users_username_live"]["unique"]

    session_columns = {column["name"] for column in inspector.get_columns("user_sessions")}
    assert "refresh_token_hash" in session_columns
    assert not any(name in session_columns for name in ("refresh_token", "token"))
    assert {fk["referred_table"]: fk["options"].get("ondelete") for fk in inspector.get_foreign_keys("user_sessions")} == {
        "users": "CASCADE"
    }
    assert {index["name"] for index in inspector.get_indexes("user_sessions")} >= {
        "ix_user_sessions_user_id",
        "ix_user_sessions_expires_at",
    }
    reset_columns = {column["name"] for column in inspector.get_columns("password_reset_tokens")}
    assert "token_hash" in reset_columns and "token" not in reset_columns


def _assert_rbac_structure_and_seed(engine: Engine) -> None:
    from app.core.authorization import SYSTEM_PERMISSIONS

    inspector = inspect(engine)
    for table, index in (
        ("role_permissions", "ix_role_permissions_active_pair"),
        ("user_roles", "ix_user_roles_active_pair"),
        ("user_permissions", "ix_user_permissions_active_pair"),
        ("roles", "ix_roles_key_live"),
        ("permissions", "ix_permissions_key_live"),
    ):
        indexes = {i["name"]: i for i in inspector.get_indexes(table)}
        assert indexes[index]["unique"], (table, index)
    with engine.connect() as connection:
        keys = set(connection.execute(text("SELECT key FROM permissions WHERE is_system AND resource_type = 'system'")).scalars())
        assert keys == {d.key for d in SYSTEM_PERMISSIONS}
        admin_permissions = connection.execute(
            text(
                "SELECT count(*) FROM role_permissions rp JOIN roles r ON r.id = rp.role_id "
                "WHERE r.key = 'administrator' AND r.is_system"
            )
        ).scalar_one()
        assert admin_permissions == len(SYSTEM_PERMISSIONS)
        # 1 from 0003, incremented by 0004 (schema), 0005 (connector) and 0006 (AI permissions).
        assert connection.execute(text("SELECT revision FROM authorization_state")).scalar_one() == 4
        # Only built-in rows: no business roles or permissions are hard-coded.
        assert connection.execute(text("SELECT count(*) FROM roles WHERE NOT is_system")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM permissions WHERE NOT is_system")).scalar_one() == 0


def _assert_schema_registry_structure(engine: Engine) -> None:
    inspector = inspect(engine)
    for table, index in (
        ("schema_sources", "ix_schema_sources_name_live"),
        ("schema_tables", "ix_schema_tables_name_live"),
        ("schema_columns", "ix_schema_columns_name_live"),
        ("schema_relationships", "ix_schema_relationships_pair_live"),
    ):
        indexes = {i["name"]: i for i in inspector.get_indexes(table)}
        assert indexes[index]["unique"], (table, index)
    source_columns = {c["name"] for c in inspector.get_columns("schema_sources")}
    # Metadata only: nothing that could hold connection details or credentials.
    assert not source_columns & {"host", "port", "username", "password", "connection_string", "url", "dsn"}
    with engine.connect() as connection:
        # No business schema is seeded.
        assert connection.execute(text("SELECT count(*) FROM schema_sources")).scalar_one() == 0


def _assert_connector_structure(engine: Engine) -> None:
    inspector = inspect(engine)
    indexes = {i["name"]: i for i in inspector.get_indexes("external_api_connectors")}
    for name in (
        "ix_external_api_connectors_name_live",
        "ix_external_api_connectors_endpoint_live",
        "ix_external_api_connectors_active_source",
    ):
        assert indexes[name]["unique"], name
    columns = {c["name"] for c in inspector.get_columns("external_api_connectors")}
    # Secrets only as ciphertext; nothing that could hold database credentials.
    assert "encrypted_credentials" in columns
    assert not columns & {"password", "api_key", "token", "credentials", "connection_string", "dsn", "username"}
    checks = {c["name"] for c in inspector.get_check_constraints("external_api_connectors")}
    assert {"ck_external_api_connectors_http_method_get", "ck_external_api_connectors_credentials_encrypted"} <= checks
    audit_columns = {c["name"] for c in inspector.get_columns("connector_audit_events")}
    assert {"sql_fingerprint", "sql_length", "error_category", "execution_reference"} <= audit_columns
    assert not audit_columns & {"sql", "exact_sql", "credential", "headers"}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM external_api_connectors")).scalar_one() == 0
        keys = set(connection.execute(text("SELECT key FROM permissions WHERE key LIKE 'connector.%'")).scalars())
        assert keys == {"connector.read", "connector.create", "connector.update", "connector.delete", "connector.test",
                        "connector.activate"}


def _assert_ai_structure(engine: Engine) -> None:
    inspector = inspect(engine)
    columns = {c["name"]: c for c in inspector.get_columns("ai_usage_records")}
    assert {"provider", "model", "status", "input_tokens", "output_tokens", "total_tokens", "latency_ms"} <= set(columns)
    # Token counts are nullable: a provider that reports nothing leaves them NULL (never estimated).
    assert all(columns[name]["nullable"] for name in ("input_tokens", "output_tokens", "total_tokens"))
    # No prompt, answer, credential or price is stored.
    assert not set(columns) & {"prompt", "messages", "content", "response", "api_key", "credential", "cost", "price"}
    assert {fk["referred_table"]: fk["options"].get("ondelete") for fk in inspector.get_foreign_keys("ai_usage_records")} == {
        "users": "SET NULL"
    }
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM ai_usage_records")).scalar_one() == 0
        keys = set(connection.execute(text("SELECT key FROM permissions WHERE key LIKE 'ai.%'")).scalars())
        assert keys == {"ai.provider.read", "ai.provider.test", "ai.usage.read"}
