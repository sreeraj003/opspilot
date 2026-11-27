"""Model conventions that can be checked without a database."""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, MetaData, Table, Uuid
from sqlalchemy.dialects.postgresql import JSONB

from app.database.base import Base
from app.database.naming import NAMING_CONVENTION
from app.models import ApplicationSetting
from tests.database.conftest import Widget


def test_all_application_models_share_one_metadata() -> None:
    assert ApplicationSetting.__table__.metadata is Base.metadata
    assert set(Base.metadata.tables) == {
        "application_settings",
        "users",
        "user_sessions",
        "retired_refresh_tokens",
        "password_reset_tokens",
        "auth_events",
        "roles",
        "permissions",
        "role_permissions",
        "user_roles",
        "user_permissions",
        "rbac_audit_events",
        "authorization_state",
        "schema_sources",
        "schema_tables",
        "schema_columns",
        "schema_relationships",
        "schema_change_events",
        "external_api_connectors",
        "connector_audit_events",
        "ai_usage_records",
        "business_terms",
        "question_understanding_events",
        "business_intents",
        "sql_generations",
        "sql_generation_events",
    }


def test_constraint_names_follow_the_naming_convention() -> None:
    table = ApplicationSetting.__table__
    names = {constraint.name for constraint in table.constraints}

    assert names == {
        "pk_application_settings",
        "uq_application_settings_key",
        "ck_application_settings_key_format",
        "ck_application_settings_description_length",
        "ck_application_settings_value_size",
        "ck_application_settings_value_not_null",
    }


def test_index_and_foreign_key_names_follow_the_naming_convention() -> None:
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table("roles", metadata, Column("id", Integer, primary_key=True))
    users = Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("role_id", ForeignKey("roles.id")),
        Column("created_at", DateTime(timezone=True), index=True),
    )

    assert {index.name for index in users.indexes} == {"ix_users_created_at"}
    assert {fk.name for fk in users.foreign_key_constraints} == {"fk_users_role_id_roles"}


def test_uuid_primary_key_and_timezone_aware_timestamps() -> None:
    columns = ApplicationSetting.__table__.c

    assert isinstance(columns.id.type, Uuid)
    assert columns.id.primary_key
    assert columns.id.server_default is not None
    for name in ("created_at", "updated_at"):
        assert isinstance(columns[name].type, DateTime)
        assert columns[name].type.timezone is True
        assert columns[name].nullable is False
        assert columns[name].server_default is not None
    assert columns.updated_at.onupdate is not None


def test_application_setting_columns_are_explicit() -> None:
    columns = ApplicationSetting.__table__.c

    assert [column.name for column in ApplicationSetting.__table__.columns] == [
        "id",
        "key",
        "value",
        "description",
        "created_at",
        "updated_at",
    ]
    assert columns.key.type.length == 128
    assert not columns.key.nullable
    assert isinstance(columns.value.type, JSONB)
    assert not columns.value.nullable
    assert columns.description.nullable


def test_soft_delete_is_opt_in() -> None:
    assert "deleted_at" not in ApplicationSetting.__table__.c
    assert "deleted_at" in Widget.__table__.c


def test_repr_never_contains_the_value() -> None:
    setting = ApplicationSetting(key="ui.theme", value={"secret_hint": "x"})

    assert repr(setting) == "ApplicationSetting(key='ui.theme')"
