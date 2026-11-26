"""External schema registry (Module 06).

OpsPilot never connects to a company's business database. Administrators
register *metadata* about it instead: which tables and columns exist, how they
relate, and what they mean in business terms. Later modules read this metadata
to understand questions and to generate and validate SQL.

* ``SchemaSource`` is one registered external database schema. It holds no
  credentials, host names or connection strings: only descriptive metadata.
  ``schema_version`` increases with every committed change to its metadata
  (see ``app.services.schema_registry_service``); SQL approvals in a later
  module are bound to it.
* ``SchemaTable``, ``SchemaColumn`` and ``SchemaRelationship`` describe the
  structure. All four are soft-deleted (``deleted_at``) so change history keeps
  pointing at them; names are unique among live rows only (case-insensitively,
  because most engines fold unquoted identifiers).
* ``SchemaChangeEvent`` is the append-only audit log and version history.

Identifiers and data types are validated by ``app.schema_registry`` before
they reach these tables; the check constraints below are a second line.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.base_model import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

SOURCE_NAME_MAX_LENGTH = 150
DIALECT_MAX_LENGTH = 64
IDENTIFIER_MAX_LENGTH = 63
DATA_TYPE_MAX_LENGTH = 64
METADATA_TEXT_MAX_LENGTH = 4000

DATABASE_TYPES = ("postgresql", "mysql", "mariadb", "sqlserver", "oracle", "sqlite", "other")
RELATIONSHIP_TYPES = ("one_to_one", "one_to_many", "many_to_one", "many_to_many")

# Letters, digits and underscores, not starting with a digit; at most 63 characters.
# The same rule as permission policies (app.authorization.policy), so a table
# registered here can always be named in a policy.
IDENTIFIER_SQL_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,62}$"


def _text_limit(column: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IS NULL OR char_length({column}) <= {METADATA_TEXT_MAX_LENGTH}", name=f"{column}_length"
    )


class SchemaSource(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "schema_sources"
    __table_args__ = (
        CheckConstraint(f"char_length(btrim(name)) BETWEEN 1 AND {SOURCE_NAME_MAX_LENGTH}", name="name_length"),
        CheckConstraint(
            "database_type IN (" + ", ".join(f"'{t}'" for t in DATABASE_TYPES) + ")", name="database_type_valid"
        ),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("jsonb_typeof(business_glossary) = 'array'", name="glossary_is_array"),
        CheckConstraint("octet_length(business_glossary::text) <= 262144", name="glossary_size"),
        _text_limit("description"),
        Index(
            "ix_schema_sources_name_live", func.lower(text("name")), unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"comment": "Registered external database schemas. Metadata only: never credentials or connections."},
    )

    name: Mapped[str] = mapped_column(String(SOURCE_NAME_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(Text)
    database_type: Mapped[str] = mapped_column(String(32))
    dialect: Mapped[str] = mapped_column(String(DIALECT_MAX_LENGTH))
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    # When the metadata (and therefore schema_version) last changed.
    metadata_updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Business terminology: [{"term", "definition", "synonyms": [...]}], validated by
    # app.schemas.schema_registry.GlossaryTerm. Assign a new list to change it.
    business_glossary: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    def __repr__(self) -> str:
        return f"SchemaSource(name={self.name!r}, version={self.schema_version})"


class SchemaTable(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "schema_tables"
    __table_args__ = (
        CheckConstraint(f"schema_name ~ '{IDENTIFIER_SQL_PATTERN}'", name="schema_name_format"),
        CheckConstraint(f"table_name ~ '{IDENTIFIER_SQL_PATTERN}'", name="table_name_format"),
        _text_limit("description"),
        _text_limit("business_purpose"),
        # A table name is unique within its schema source and schema namespace.
        Index(
            "ix_schema_tables_name_live",
            "schema_source_id",
            func.lower(text("schema_name")),
            func.lower(text("table_name")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"comment": "Tables of a registered external schema (metadata only)."},
    )

    schema_source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schema_sources.id", ondelete="CASCADE"))
    schema_name: Mapped[str] = mapped_column(String(IDENTIFIER_MAX_LENGTH))
    table_name: Mapped[str] = mapped_column(String(IDENTIFIER_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(Text)
    business_purpose: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    def __repr__(self) -> str:
        return f"SchemaTable({self.qualified_name!r})"


class SchemaColumn(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "schema_columns"
    __table_args__ = (
        CheckConstraint(f"column_name ~ '{IDENTIFIER_SQL_PATTERN}'", name="column_name_format"),
        CheckConstraint(
            f"char_length(data_type) BETWEEN 1 AND {DATA_TYPE_MAX_LENGTH}", name="data_type_length"
        ),
        CheckConstraint("NOT is_primary_key OR NOT is_nullable", name="primary_key_not_nullable"),
        CheckConstraint("ordinal_position >= 0", name="ordinal_position_non_negative"),
        _text_limit("description"),
        _text_limit("business_meaning"),
        Index(
            "ix_schema_columns_name_live",
            "table_id",
            func.lower(text("column_name")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"comment": "Columns of registered tables (metadata only)."},
    )

    table_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schema_tables.id", ondelete="CASCADE"))
    column_name: Mapped[str] = mapped_column(String(IDENTIFIER_MAX_LENGTH))
    # Normalised by app.schema_registry.data_types (lower case, single spaces).
    data_type: Mapped[str] = mapped_column(String(DATA_TYPE_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(Text)
    business_meaning: Mapped[str | None] = mapped_column(Text)
    is_primary_key: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    is_foreign_key: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    is_nullable: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    is_sensitive: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # Display and export order within the table.
    ordinal_position: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    def __repr__(self) -> str:
        return f"SchemaColumn({self.column_name!r}, {self.data_type!r})"


class SchemaRelationship(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "schema_relationships"
    __table_args__ = (
        CheckConstraint(
            "relationship_type IN (" + ", ".join(f"'{t}'" for t in RELATIONSHIP_TYPES) + ")",
            name="relationship_type_valid",
        ),
        # A table may reference itself (employees.manager_id -> employees.id), a column may not.
        CheckConstraint("source_column_id <> target_column_id", name="not_same_column"),
        _text_limit("description"),
        Index(
            "ix_schema_relationships_pair_live",
            "source_column_id",
            "target_column_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_schema_relationships_schema_source_id", "schema_source_id"),
        Index("ix_schema_relationships_source_table_id", "source_table_id"),
        Index("ix_schema_relationships_target_table_id", "target_table_id"),
        Index("ix_schema_relationships_target_column_id", "target_column_id"),
        {"comment": "Relationships between registered tables. Both endpoints belong to the same schema source."},
    )

    schema_source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schema_sources.id", ondelete="CASCADE"))
    source_table_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schema_tables.id", ondelete="CASCADE"))
    source_column_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schema_columns.id", ondelete="CASCADE"))
    target_table_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schema_tables.id", ondelete="CASCADE"))
    target_column_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schema_columns.id", ondelete="CASCADE"))
    relationship_type: Mapped[str] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class SchemaChangeType:
    SCHEMA_CREATED = "schema_created"
    SCHEMA_UPDATED = "schema_updated"
    SCHEMA_DELETED = "schema_deleted"
    TABLE_CREATED = "table_created"
    TABLE_UPDATED = "table_updated"
    TABLE_DELETED = "table_deleted"
    COLUMN_CREATED = "column_created"
    COLUMN_UPDATED = "column_updated"
    COLUMN_DELETED = "column_deleted"
    RELATIONSHIP_CREATED = "relationship_created"
    RELATIONSHIP_UPDATED = "relationship_updated"
    RELATIONSHIP_DELETED = "relationship_deleted"
    SCHEMA_IMPORTED = "schema_imported"
    SCHEMA_EXPORTED = "schema_exported"


class SchemaChangeEvent(UUIDPrimaryKeyMixin, Base):
    """Append-only: one row per committed schema change (and per export).

    ``schema_version`` is the source's version after the change, and
    ``version_incremented`` says whether this change produced it.
    """

    __tablename__ = "schema_change_events"
    __table_args__ = (
        Index("ix_schema_change_events_source_created", "schema_source_id", "created_at"),
        Index("ix_schema_change_events_created_at", "created_at"),
        {"comment": "Append-only audit log and version history of schema registry changes."},
    )

    schema_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schema_sources.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(64))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # "schema", "table", "column", "relationship" or "import".
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID | None] = mapped_column()
    schema_version: Mapped[int] = mapped_column(Integer)
    version_incremented: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # One human-readable sentence, e.g. "Added column orders.total_amount".
    summary: Mapped[str] = mapped_column(Text)
    # Changed fields (before/after), names and import counts. Never credentials.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=100)
