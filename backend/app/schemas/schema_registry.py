"""Request and response contracts of the schema registry (Module 06).

Write schemas forbid unknown fields and check identifiers and data types
here, so malformed metadata never reaches a service. Cross-item rules
(uniqueness, primary keys, relationships) are checked by
``app.schema_registry.validation`` on the whole resulting schema.

The JSON import format (``SchemaImportDocument``) is documented in
``docs/SCHEMA_IMPORT_FORMAT.md``; the export endpoint produces the same format.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.models.schema_registry import (
    DATABASE_TYPES,
    DIALECT_MAX_LENGTH,
    METADATA_TEXT_MAX_LENGTH,
    RELATIONSHIP_TYPES,
    SOURCE_NAME_MAX_LENGTH,
)
from app.schema_registry.data_types import DataTypeError, is_identifier, parse_data_type

_WRITE_CONFIG = ConfigDict(extra="forbid", hide_input_in_errors=True)

DatabaseType = Literal["postgresql", "mysql", "mariadb", "sqlserver", "oracle", "sqlite", "other"]
RelationshipType = Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"]
ImportStrategy = Literal["create_only", "merge", "replace"]
assert set(DatabaseType.__args__) == set(DATABASE_TYPES)  # type: ignore[attr-defined]
assert set(RelationshipType.__args__) == set(RELATIONSHIP_TYPES)  # type: ignore[attr-defined]

# Hard caps on list sizes. The configurable limits (SCHEMA_MAX_*) are lower and
# are applied by the validation service; these stop absurd payloads early.
_HARD_MAX_TABLES = 20000
_HARD_MAX_COLUMNS = 5000
_HARD_MAX_RELATIONSHIPS = 50000
MAX_GLOSSARY_TERMS = 500


def _identifier(value: str) -> str:
    value = value.strip()
    if not is_identifier(value):
        raise ValueError(
            "Must be a plain identifier: letters, digits and underscores, starting with a letter or underscore, "
            "at most 63 characters."
        )
    return value


def _data_type(value: str) -> str:
    try:
        return parse_data_type(value).normalized
    except DataTypeError as exc:
        raise ValueError(str(exc)) from None


def _clean_name(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("Must not be empty.")
    return value


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


Identifier = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_identifier)]
DataTypeText = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_data_type)]
SourceName = Annotated[str, Field(min_length=1, max_length=SOURCE_NAME_MAX_LENGTH), AfterValidator(_clean_name)]
MetadataText = Annotated[str, Field(max_length=METADATA_TEXT_MAX_LENGTH), AfterValidator(_clean_text)]
Dialect = Annotated[
    str, Field(min_length=1, max_length=DIALECT_MAX_LENGTH, pattern=r"^[a-z0-9][a-z0-9_.+ -]*$")
]
ShortText = Annotated[str, Field(min_length=1, max_length=100), AfterValidator(_clean_name)]


# --- business glossary ----------------------------------------------------------------------------


class GlossaryTerm(BaseModel):
    """A business term the AI should understand, e.g. "GMV" or "active customer"."""

    model_config = _WRITE_CONFIG

    term: ShortText
    definition: Annotated[str, Field(min_length=1, max_length=2000), AfterValidator(_clean_name)]
    synonyms: Annotated[list[ShortText], Field(max_length=20)] = []


def _unique_terms(terms: list[GlossaryTerm] | None) -> list[GlossaryTerm] | None:
    if terms is None:
        return None
    seen: set[str] = set()
    for term in terms:
        key = term.term.lower()
        if key in seen:
            raise ValueError(f"The term '{term.term}' is listed more than once.")
        seen.add(key)
    return terms


Glossary = Annotated[list[GlossaryTerm], Field(max_length=MAX_GLOSSARY_TERMS), AfterValidator(_unique_terms)]


# --- schema sources ------------------------------------------------------------------------------------


class SchemaSourceCreate(BaseModel):
    model_config = _WRITE_CONFIG

    name: SourceName
    description: MetadataText | None = None
    database_type: DatabaseType
    # Defaults to the database type, e.g. "postgresql"; may be more specific ("postgresql-16", "tsql").
    dialect: Dialect | None = None
    is_active: bool = True
    business_glossary: Glossary = []


class SchemaSourceUpdate(BaseModel):
    model_config = _WRITE_CONFIG

    name: SourceName | None = None
    description: MetadataText | None = None
    database_type: DatabaseType | None = None
    dialect: Dialect | None = None
    is_active: bool | None = None
    business_glossary: Glossary | None = None


class SchemaSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    database_type: str
    dialect: str
    schema_version: int
    metadata_updated_at: datetime
    is_active: bool
    business_glossary: list[GlossaryTerm]
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    table_count: int = 0
    column_count: int = 0
    relationship_count: int = 0


class SchemaSourcePage(BaseModel):
    items: list[SchemaSourceRead]
    total: int
    offset: int
    limit: int


# --- tables ------------------------------------------------------------------------------------------------


class SchemaColumnCreate(BaseModel):
    model_config = _WRITE_CONFIG

    column_name: Identifier
    data_type: DataTypeText
    description: MetadataText | None = None
    business_meaning: MetadataText | None = None
    is_primary_key: bool = False
    is_foreign_key: bool = False
    # Defaults to false for primary-key columns and true otherwise.
    is_nullable: bool | None = None
    is_sensitive: bool = False
    is_active: bool = True
    # Defaults to after the table's last column.
    ordinal_position: Annotated[int, Field(ge=0, le=100000)] | None = None


class SchemaColumnUpdate(BaseModel):
    model_config = _WRITE_CONFIG

    column_name: Identifier | None = None
    data_type: DataTypeText | None = None
    description: MetadataText | None = None
    business_meaning: MetadataText | None = None
    is_primary_key: bool | None = None
    is_foreign_key: bool | None = None
    is_nullable: bool | None = None
    is_sensitive: bool | None = None
    is_active: bool | None = None
    ordinal_position: Annotated[int, Field(ge=0, le=100000)] | None = None


class SchemaColumnRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    table_id: uuid.UUID
    column_name: str
    data_type: str
    data_type_category: str = "other"
    description: str | None
    business_meaning: str | None
    is_primary_key: bool
    is_foreign_key: bool
    is_nullable: bool
    is_sensitive: bool
    is_active: bool
    ordinal_position: int
    created_at: datetime
    updated_at: datetime


class SchemaColumnPage(BaseModel):
    items: list[SchemaColumnRead]
    total: int
    offset: int
    limit: int


class SchemaTableCreate(BaseModel):
    model_config = _WRITE_CONFIG

    schema_name: Identifier
    table_name: Identifier
    description: MetadataText | None = None
    business_purpose: MetadataText | None = None
    is_active: bool = True
    # Optional: register the table's columns in the same change.
    columns: Annotated[list[SchemaColumnCreate], Field(max_length=_HARD_MAX_COLUMNS)] = []


class SchemaTableUpdate(BaseModel):
    model_config = _WRITE_CONFIG

    schema_name: Identifier | None = None
    table_name: Identifier | None = None
    description: MetadataText | None = None
    business_purpose: MetadataText | None = None
    is_active: bool | None = None


class SchemaTableRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    schema_source_id: uuid.UUID
    schema_name: str
    table_name: str
    qualified_name: str
    description: str | None
    business_purpose: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    column_count: int = 0
    relationship_count: int = 0


class SchemaTableDetail(SchemaTableRead):
    columns: list[SchemaColumnRead]


class SchemaTablePage(BaseModel):
    items: list[SchemaTableRead]
    total: int
    offset: int
    limit: int


# --- relationships -------------------------------------------------------------------------------------------


class SchemaRelationshipCreate(BaseModel):
    model_config = _WRITE_CONFIG

    source_table_id: uuid.UUID
    source_column_id: uuid.UUID
    target_table_id: uuid.UUID
    target_column_id: uuid.UUID
    relationship_type: RelationshipType
    description: MetadataText | None = None
    is_active: bool = True


class SchemaRelationshipUpdate(BaseModel):
    model_config = _WRITE_CONFIG

    source_table_id: uuid.UUID | None = None
    source_column_id: uuid.UUID | None = None
    target_table_id: uuid.UUID | None = None
    target_column_id: uuid.UUID | None = None
    relationship_type: RelationshipType | None = None
    description: MetadataText | None = None
    is_active: bool | None = None


class RelationshipEndpointRead(BaseModel):
    table_id: uuid.UUID
    column_id: uuid.UUID
    schema_name: str
    table_name: str
    column_name: str
    data_type: str
    is_active: bool


class SchemaRelationshipRead(BaseModel):
    id: uuid.UUID
    schema_source_id: uuid.UUID
    source: RelationshipEndpointRead
    target: RelationshipEndpointRead
    relationship_type: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SchemaRelationshipPage(BaseModel):
    items: list[SchemaRelationshipRead]
    total: int
    offset: int
    limit: int


# --- outline, summary, version and history ---------------------------------------------------------------------------


class OutlineColumn(BaseModel):
    id: uuid.UUID
    column_name: str
    data_type: str
    data_type_category: str
    is_primary_key: bool
    is_foreign_key: bool
    is_nullable: bool
    is_sensitive: bool
    is_active: bool


class OutlineTable(BaseModel):
    id: uuid.UUID
    schema_name: str
    table_name: str
    is_active: bool
    columns: list[OutlineColumn]


class SchemaOutline(BaseModel):
    """Every live table and column, compact: what pickers such as the relationship form need."""

    schema_id: uuid.UUID
    schema_version: int
    tables: list[OutlineTable]


class ValidationIssueRead(BaseModel):
    code: str
    message: str
    location: list[str]
    severity: Literal["error", "warning"]


class SchemaCounts(BaseModel):
    tables: int
    active_tables: int
    columns: int
    active_columns: int
    primary_key_columns: int
    foreign_key_columns: int
    sensitive_columns: int
    relationships: int
    active_relationships: int
    schema_namespaces: list[str]
    glossary_terms: int


class SchemaSummary(BaseModel):
    schema_id: uuid.UUID
    name: str
    database_type: str
    dialect: str
    is_active: bool
    schema_version: int
    metadata_updated_at: datetime
    counts: SchemaCounts
    # The current metadata checked by the validation service.
    issues: list[ValidationIssueRead]


class SchemaChangeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    entity_type: str
    entity_id: uuid.UUID | None
    schema_version: int
    version_incremented: bool
    summary: str
    details: dict[str, Any]
    actor_user_id: uuid.UUID | None
    actor_name: str | None = None
    created_at: datetime


class SchemaChangePage(BaseModel):
    items: list[SchemaChangeRead]
    total: int
    offset: int
    limit: int


class SchemaVersionRead(BaseModel):
    schema_id: uuid.UUID
    schema_version: int
    metadata_updated_at: datetime
    # The change that produced the current version, if any was recorded.
    last_change: SchemaChangeRead | None


# --- import format -------------------------------------------------------------------------------------------------


class ImportColumn(BaseModel):
    model_config = _WRITE_CONFIG

    column_name: Identifier
    data_type: DataTypeText
    description: MetadataText | None = None
    business_meaning: MetadataText | None = None
    is_primary_key: bool = False
    is_foreign_key: bool = False
    # Defaults to false for primary-key columns and true otherwise.
    is_nullable: bool | None = None
    is_sensitive: bool = False
    is_active: bool = True


class ImportTable(BaseModel):
    model_config = _WRITE_CONFIG

    # Defaults to the document's schema_name.
    schema_name: Identifier | None = None
    table_name: Identifier
    description: MetadataText | None = None
    business_purpose: MetadataText | None = None
    is_active: bool = True
    columns: Annotated[list[ImportColumn], Field(max_length=_HARD_MAX_COLUMNS)] = []


class ImportRelationship(BaseModel):
    model_config = _WRITE_CONFIG

    # Default to the document's schema_name.
    source_schema: Identifier | None = None
    source_table: Identifier
    source_column: Identifier
    target_schema: Identifier | None = None
    target_table: Identifier
    target_column: Identifier
    relationship_type: RelationshipType
    description: MetadataText | None = None
    is_active: bool = True


class ImportSourceInfo(BaseModel):
    """Informational only (written by export); import never changes the source from it."""

    model_config = _WRITE_CONFIG

    name: str | None = Field(default=None, max_length=SOURCE_NAME_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=METADATA_TEXT_MAX_LENGTH)
    database_type: str | None = Field(default=None, max_length=32)
    dialect: str | None = Field(default=None, max_length=DIALECT_MAX_LENGTH)


class SchemaImportDocument(BaseModel):
    model_config = _WRITE_CONFIG

    format_version: Literal[1] = 1
    # Default namespace for tables and relationships that do not name one.
    schema_name: Identifier | None = None
    tables: Annotated[list[ImportTable], Field(max_length=_HARD_MAX_TABLES)] = []
    relationships: Annotated[list[ImportRelationship], Field(max_length=_HARD_MAX_RELATIONSHIPS)] = []
    # Omitted: the glossary is left as it is.
    business_glossary: Glossary | None = None
    # Written by export; ignored by import.
    source: ImportSourceInfo | None = None
    schema_version: int | None = Field(default=None, ge=1)
    exported_at: datetime | None = None

    @model_validator(mode="after")
    def _namespaces_resolvable(self) -> "SchemaImportDocument":
        if self.schema_name is None:
            for index, table in enumerate(self.tables):
                if table.schema_name is None:
                    raise ValueError(
                        f"tables[{index}] ('{table.table_name}') has no schema_name and the document has no "
                        "default schema_name."
                    )
            for index, relationship in enumerate(self.relationships):
                if relationship.source_schema is None or relationship.target_schema is None:
                    raise ValueError(
                        f"relationships[{index}] needs source_schema and target_schema, or the document needs a "
                        "default schema_name."
                    )
        return self


class SchemaImportOptions(BaseModel):
    """The import request envelope; ``document`` is validated separately so that a
    dry run can report every problem in it instead of failing on the first."""

    model_config = _WRITE_CONFIG

    strategy: ImportStrategy = "create_only"
    dry_run: bool = False
    # Required (true) for the replace strategy, which removes what the document does not list.
    confirm_replace: bool = False
    # When set, the import is refused if the schema changed since (e.g. since the preview).
    expected_schema_version: int | None = Field(default=None, ge=1)
    document: dict[str, Any]


class ImportCount(BaseModel):
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0


class ImportCounts(BaseModel):
    tables: ImportCount = ImportCount()
    columns: ImportCount = ImportCount()
    relationships: ImportCount = ImportCount()
    glossary_terms: ImportCount = ImportCount()


class ImportChange(BaseModel):
    action: Literal["create", "update", "remove"]
    entity_type: Literal["table", "column", "relationship", "glossary_term"]
    name: str
    fields: list[str] = []


class SchemaImportResult(BaseModel):
    strategy: ImportStrategy
    dry_run: bool
    applied: bool
    valid: bool
    schema_version_before: int
    schema_version_after: int
    version_incremented: bool
    counts: ImportCounts
    changes: list[ImportChange]
    changes_truncated: bool = False
    errors: list[ValidationIssueRead]
    warnings: list[ValidationIssueRead]


# --- AI-ready representation ---------------------------------------------------------------------------------------


class AiColumn(BaseModel):
    name: str
    type: str
    nullable: bool
    description: str | None = None
    business_meaning: str | None = None
    primary_key: bool | None = None
    foreign_key: bool | None = None
    sensitive: bool | None = None


class AiRelationshipEnd(BaseModel):
    schema_name: str = Field(serialization_alias="schema")
    table: str
    column: str


class AiTableRelationship(BaseModel):
    type: str
    column: str
    references: AiRelationshipEnd
    description: str | None = None


class AiTable(BaseModel):
    schema_name: str = Field(serialization_alias="schema")
    table: str
    description: str | None = None
    business_purpose: str | None = None
    columns: list[AiColumn]
    relationships: list[AiTableRelationship]


class AiRelationship(BaseModel):
    type: str
    source: AiRelationshipEnd
    target: AiRelationshipEnd
    description: str | None = None


class AiBusinessTerm(BaseModel):
    term: str
    definition: str
    synonyms: list[str] = []


class AiSchemaRepresentation(BaseModel):
    """Structured metadata for AI and SQL-generation modules. Never a prompt."""

    schema_id: uuid.UUID
    schema_source: str
    database_type: str
    dialect: str
    schema_version: int
    tables: list[AiTable]
    relationships: list[AiRelationship]
    business_terms: list[AiBusinessTerm]
