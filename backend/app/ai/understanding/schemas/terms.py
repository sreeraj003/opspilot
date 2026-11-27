"""Request and response contracts for business terminology (``/api/v1/business-terms``)."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.ai.understanding.schemas.intent import (
    AggregationType,
    QualifiedColumn,
    QualifiedTable,
    TermKind,
)
from app.ai.understanding.validators.safety import ensure_safe_text
from app.models.business_term import TERM_DESCRIPTION_MAX_LENGTH, TERM_GUIDANCE_MAX_LENGTH, TERM_NAME_MAX_LENGTH
from app.repositories.base_repository import MAX_PAGE_SIZE

_WRITE = ConfigDict(extra="forbid", hide_input_in_errors=True)

MAX_SYNONYMS = 20
MAX_RELATED_TABLES = 20
MAX_RELATED_COLUMNS = 50

TermName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=TERM_NAME_MAX_LENGTH),
    AfterValidator(ensure_safe_text),
]
TermDescription = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=TERM_DESCRIPTION_MAX_LENGTH),
    AfterValidator(ensure_safe_text),
]
TermGuidance = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=TERM_GUIDANCE_MAX_LENGTH),
    AfterValidator(ensure_safe_text),
]


def _unique_ci(values: list[str] | None, what: str) -> list[str] | None:
    if values is None:
        return None
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if key in seen:
            raise ValueError(f"The {what} '{value}' is listed more than once.")
        seen.add(key)
    return values


def _unique_synonyms(values: list[str] | None) -> list[str] | None:
    return _unique_ci(values, "synonym")


def _unique_tables(values: list[str] | None) -> list[str] | None:
    return _unique_ci(values, "table")


def _unique_columns(values: list[str] | None) -> list[str] | None:
    return _unique_ci(values, "column")


Synonyms = Annotated[list[TermName], Field(max_length=MAX_SYNONYMS), AfterValidator(_unique_synonyms)]
RelatedTables = Annotated[list[QualifiedTable], Field(max_length=MAX_RELATED_TABLES), AfterValidator(_unique_tables)]
RelatedColumns = Annotated[
    list[QualifiedColumn], Field(max_length=MAX_RELATED_COLUMNS), AfterValidator(_unique_columns)
]


class BusinessTermCreate(BaseModel):
    model_config = _WRITE

    schema_source_id: uuid.UUID
    name: TermName
    description: TermDescription
    kind: TermKind = TermKind.METRIC
    synonyms: Synonyms = []
    related_tables: RelatedTables = []
    related_columns: RelatedColumns = []
    default_aggregation: AggregationType | None = None
    aggregation_guidance: TermGuidance | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _check(self) -> "BusinessTermCreate":
        _check_term(self.name, self.synonyms, self.related_tables, self.related_columns)
        return self


class BusinessTermUpdate(BaseModel):
    """Partial update. ``schema_source_id`` cannot change: delete and recreate the term instead."""

    model_config = _WRITE

    name: TermName | None = None
    description: TermDescription | None = None
    kind: TermKind | None = None
    synonyms: Synonyms | None = None
    related_tables: RelatedTables | None = None
    related_columns: RelatedColumns | None = None
    default_aggregation: AggregationType | None = None
    aggregation_guidance: TermGuidance | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _not_null(self) -> "BusinessTermUpdate":
        for field in ("name", "description", "kind", "synonyms", "related_tables", "related_columns", "is_active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


def _check_term(name: str, synonyms: list[str], tables: list[str], columns: list[str]) -> None:
    if any(s.lower() == name.lower() for s in synonyms):
        raise ValueError("A synonym must differ from the term's name.")
    if not tables and not columns:
        raise ValueError("Associate the term with at least one registered table or column.")


class BusinessTermRead(BaseModel):
    id: uuid.UUID
    schema_source_id: uuid.UUID
    schema_source_name: str | None
    name: str
    description: str
    kind: TermKind
    synonyms: list[str]
    related_tables: list[str]
    related_columns: list[str]
    default_aggregation: AggregationType | None
    aggregation_guidance: str | None
    is_active: bool
    # References that no longer match an active registered table or column (schema changed since).
    stale_references: list[str] = []
    created_at: datetime
    updated_at: datetime


class BusinessTermPage(BaseModel):
    items: list[BusinessTermRead]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=MAX_PAGE_SIZE)


class SchemaFieldColumn(BaseModel):
    name: str
    data_type: str
    description: str | None = None
    sensitive: bool = False


class SchemaFieldTable(BaseModel):
    table: str  # schema.table
    description: str | None = None
    columns: list[SchemaFieldColumn]


class SchemaFieldsRead(BaseModel):
    """Active registered tables and columns of one source, for associating terms with fields."""

    schema_source_id: uuid.UUID
    schema_source_name: str
    schema_version: int
    tables: list[SchemaFieldTable]
