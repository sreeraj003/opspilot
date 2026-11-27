"""Business terminology: administrator-defined terms and how questions match them.

Rules:

* A term belongs to one schema source. Its related tables and columns must be
  active, registered fields of that source when the term is saved; if the
  schema changes later, the stale references are reported (``stale_references``)
  and ignored during understanding.
* Names are unique per source among live terms (case-insensitively), and a
  synonym may not equal another live term's name. The same synonym on two
  terms is allowed on purpose: it is how an administrator records that a word
  ("sales") has several meanings, and the clarification engine then asks which
  one the user means instead of choosing silently.
* Deleting a term soft-deletes it.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.understanding.normalizers.text import singular, words
from app.ai.understanding.schemas.terms import (
    BusinessTermCreate,
    BusinessTermPage,
    BusinessTermRead,
    BusinessTermUpdate,
    SchemaFieldColumn,
    SchemaFieldsRead,
    SchemaFieldTable,
    _check_term,
)
from app.ai.understanding.services.schema_context import SchemaContext
from app.core.exceptions import ConflictError, InvalidInputError, NotFoundError
from app.database.transaction import transaction
from app.models.business_term import BusinessTerm
from app.models.user import User
from app.repositories.business_term_repository import BusinessTermRepository, TermQuery
from app.services.schema_registry_service import SchemaRegistryService

logger = logging.getLogger("app.ai.understanding")

_UNIQUE_VIOLATION = "23505"


class BusinessTermNotFoundError(NotFoundError):
    code = "BUSINESS_TERM_NOT_FOUND"
    message = "The business term was not found."


class BusinessTermConflictError(ConflictError):
    code = "BUSINESS_TERM_CONFLICT"


class BusinessTermInvalidError(InvalidInputError):
    code = "BUSINESS_TERM_INVALID"
    message = "The business term is not valid."


@dataclass(frozen=True, slots=True)
class TermMatch:
    """A term whose name or a synonym occurs in the question."""

    term: BusinessTerm
    matched_text: str


def stale_references(term: BusinessTerm, context: SchemaContext) -> list[str]:
    stale = [t for t in term.related_tables if context.table(t) is None]
    stale += [c for c in term.related_columns if context.resolve_column(c) is None]
    return stale


def term_matches(question: str, terms: Sequence[BusinessTerm]) -> list[TermMatch]:
    """Active terms named in the question (name or synonym, whole words, case-insensitive).

    Longer phrases win: in "net sales" the term "Net sales" matches and the
    synonym "sales" of another term does not. A phrase that several terms share
    matches all of them (see ``ambiguous_phrases``).
    """
    phrases: dict[tuple[str, ...], list[tuple[BusinessTerm, str]]] = {}
    for term in terms:
        for text in (term.name, *term.synonyms):
            key = tuple(singular(w) for w in words(text))
            if key:
                phrases.setdefault(key, []).append((term, text))
    remaining: list[str | None] = [singular(w) for w in words(question)]
    matches: list[TermMatch] = []
    for key in sorted(phrases, key=lambda k: (-len(k), k)):
        n = len(key)
        hits = [i for i in range(len(remaining) - n + 1) if tuple(remaining[i : i + n]) == key]
        if not hits:
            continue
        for term, text in phrases[key]:
            if all(m.term.id != term.id for m in matches):
                matches.append(TermMatch(term, text))
        for i in hits:
            remaining[i : i + n] = [None] * n
    return matches


def ambiguous_phrases(matches: Sequence[TermMatch]) -> dict[str, list[BusinessTerm]]:
    """Phrases in the question that match more than one term (for example "sales")."""
    by_phrase: dict[str, list[BusinessTerm]] = {}
    for match in matches:
        by_phrase.setdefault(" ".join(words(match.matched_text)), []).append(match.term)
    return {phrase: terms for phrase, terms in by_phrase.items() if len({t.id for t in terms}) > 1}


class TerminologyService:
    def __init__(self, session: AsyncSession, schemas: SchemaRegistryService) -> None:
        self._session = session
        self._schemas = schemas
        self._terms = BusinessTermRepository(session)

    # --- reads ------------------------------------------------------------------------------------------

    async def list_terms(self, query: TermQuery, *, offset: int, limit: int) -> BusinessTermPage:
        page = await self._terms.page(query, offset=offset, limit=limit)
        contexts: dict[uuid.UUID, SchemaContext] = {}
        items = []
        for term, source_name in page.items:
            context = contexts.get(term.schema_source_id) or await self.schema_context(term.schema_source_id)
            contexts[term.schema_source_id] = context
            items.append(self._read(term, source_name, context))
        return BusinessTermPage(items=items, total=page.total, offset=page.offset, limit=page.limit)

    async def get_term(self, term_id: uuid.UUID) -> BusinessTermRead:
        term = await self._term(term_id)
        context = await self.schema_context(term.schema_source_id)
        return self._read(term, context.source_name, context)

    async def active_terms(self, schema_source_id: uuid.UUID) -> list[BusinessTerm]:
        return list(await self._terms.for_source(schema_source_id, active_only=True))

    async def schema_context(self, schema_source_id: uuid.UUID) -> SchemaContext:
        source, loaded = await self._schemas.outline(schema_source_id)
        return SchemaContext.build(source, loaded)

    async def schema_fields(self, schema_source_id: uuid.UUID) -> SchemaFieldsRead:
        context = await self.schema_context(schema_source_id)
        return SchemaFieldsRead(
            schema_source_id=context.source_id,
            schema_source_name=context.source_name,
            schema_version=context.schema_version,
            tables=[
                SchemaFieldTable(
                    table=table.qualified,
                    description=table.description,
                    columns=[
                        SchemaFieldColumn(
                            name=c.name, data_type=c.data_type, description=c.description, sensitive=c.is_sensitive
                        )
                        for c in table.columns.values()
                    ],
                )
                for table in context.tables.values()
            ],
        )

    # --- writes -----------------------------------------------------------------------------------------

    async def create_term(self, data: BusinessTermCreate, actor: User) -> BusinessTermRead:
        context = await self.schema_context(data.schema_source_id)
        tables, columns = self._checked_references(context, data.related_tables, data.related_columns)
        try:
            async with transaction(self._session):
                await self._check_names(data.schema_source_id, data.name, data.synonyms, exclude=None)
                term = await self._terms.create(
                    schema_source_id=data.schema_source_id,
                    name=data.name,
                    description=data.description,
                    kind=data.kind.value,
                    synonyms=list(data.synonyms),
                    related_tables=tables,
                    related_columns=columns,
                    default_aggregation=data.default_aggregation.value if data.default_aggregation else None,
                    aggregation_guidance=data.aggregation_guidance,
                    is_active=data.is_active,
                    created_by=actor.id,
                    updated_by=actor.id,
                )
        except IntegrityError as exc:
            raise _conflict(exc) from None
        await self._session.refresh(term)
        self._log("business_term.created", term, actor)
        return self._read(term, context.source_name, context)

    async def update_term(self, term_id: uuid.UUID, data: BusinessTermUpdate, actor: User) -> BusinessTermRead:
        term = await self._term(term_id)
        context = await self.schema_context(term.schema_source_id)
        changes: dict[str, Any] = {}
        fields = data.model_fields_set
        name = data.name if "name" in fields and data.name is not None else term.name
        synonyms = list(data.synonyms) if "synonyms" in fields and data.synonyms is not None else list(term.synonyms)
        tables_in = list(data.related_tables) if "related_tables" in fields and data.related_tables is not None else list(term.related_tables)
        columns_in = list(data.related_columns) if "related_columns" in fields and data.related_columns is not None else list(term.related_columns)
        try:
            _check_term(name, synonyms, tables_in, columns_in)
        except ValueError as exc:
            raise BusinessTermInvalidError(str(exc)) from None
        if {"related_tables", "related_columns"} & fields:
            tables, columns = self._checked_references(context, tables_in, columns_in)
            changes["related_tables"], changes["related_columns"] = tables, columns
        for field in ("name", "description", "aggregation_guidance", "is_active"):
            if field in fields:
                changes[field] = getattr(data, field)
        if "synonyms" in fields:
            changes["synonyms"] = synonyms
        if "kind" in fields and data.kind is not None:
            changes["kind"] = data.kind.value
        if "default_aggregation" in fields:
            changes["default_aggregation"] = data.default_aggregation.value if data.default_aggregation else None
        if not changes:
            return self._read(term, context.source_name, context)
        changes["updated_by"] = actor.id
        try:
            async with transaction(self._session):
                if "name" in changes or "synonyms" in changes:
                    await self._check_names(term.schema_source_id, name, synonyms, exclude=term.id)
                await self._terms.update(term, changes)
        except IntegrityError as exc:
            raise _conflict(exc) from None
        await self._session.refresh(term)
        self._log("business_term.updated", term, actor, fields=sorted(k for k in changes if k != "updated_by"))
        return self._read(term, context.source_name, context)

    async def delete_term(self, term_id: uuid.UUID, actor: User) -> None:
        async with transaction(self._session):
            term = await self._term(term_id)
            term.updated_by = actor.id
            await self._terms.delete(term)
        self._log("business_term.deleted", term, actor)

    # --- helpers ----------------------------------------------------------------------------------------

    async def _term(self, term_id: uuid.UUID) -> BusinessTerm:
        term = await self._terms.get(term_id)
        if term is None:
            raise BusinessTermNotFoundError()
        return term

    @staticmethod
    def _checked_references(context: SchemaContext, tables: Sequence[str], columns: Sequence[str]) -> tuple[list[str], list[str]]:
        """Canonical names of the referenced fields; 422 listing every unknown one."""
        problems: list[dict[str, Any]] = []
        canonical_tables: list[str] = []
        for index, name in enumerate(tables):
            table = context.table(name)
            if table is None:
                problems.append({"location": ["related_tables", str(index)], "message": f"Table '{name}' is not an active registered table of this schema."})
            elif table.qualified not in canonical_tables:
                canonical_tables.append(table.qualified)
        canonical_columns: list[str] = []
        for index, name in enumerate(columns):
            resolved = context.resolve_column(name)
            if resolved is None:
                problems.append({"location": ["related_columns", str(index)], "message": f"Column '{name}' is not an active registered column of this schema."})
                continue
            table, column = resolved
            canonical = f"{table.qualified}.{column.name}"
            if canonical not in canonical_columns:
                canonical_columns.append(canonical)
            if table.qualified not in canonical_tables:
                canonical_tables.append(table.qualified)  # a column's table is always related too
        if problems:
            raise BusinessTermInvalidError("The term refers to fields that are not registered.", details=problems)
        return canonical_tables, canonical_columns

    async def _check_names(self, source_id: uuid.UUID, name: str, synonyms: Sequence[str], *, exclude: uuid.UUID | None) -> None:
        others = [t for t in await self._terms.for_source(source_id) if t.id != exclude]
        lower_name = name.lower()
        for other in others:
            if other.name.lower() == lower_name:
                raise BusinessTermConflictError("A business term with this name already exists for this schema.")
            if any(s.lower() == lower_name for s in other.synonyms):
                raise BusinessTermConflictError(f"'{name}' is already a synonym of the term '{other.name}'.")
            if any(s.lower() == other.name.lower() for s in synonyms):
                raise BusinessTermConflictError(f"A synonym repeats the name of the term '{other.name}'.")

    @staticmethod
    def _read(term: BusinessTerm, source_name: str | None, context: SchemaContext) -> BusinessTermRead:
        return BusinessTermRead(
            id=term.id,
            schema_source_id=term.schema_source_id,
            schema_source_name=source_name,
            name=term.name,
            description=term.description,
            kind=term.kind,  # type: ignore[arg-type]
            synonyms=list(term.synonyms),
            related_tables=list(term.related_tables),
            related_columns=list(term.related_columns),
            default_aggregation=term.default_aggregation,  # type: ignore[arg-type]
            aggregation_guidance=term.aggregation_guidance,
            is_active=term.is_active,
            stale_references=stale_references(term, context),
            created_at=term.created_at,
            updated_at=term.updated_at,
        )

    @staticmethod
    def _log(event: str, term: BusinessTerm, actor: User, **extra: Any) -> None:
        logger.info(
            "Business term changed",
            extra={"event": event, "term_id": str(term.id), "schema_source_id": str(term.schema_source_id),
                   "actor_id": str(actor.id), **extra},
        )


def _conflict(exc: IntegrityError) -> Exception:
    if getattr(exc.orig, "sqlstate", None) == _UNIQUE_VIOLATION:
        return BusinessTermConflictError("A business term with this name already exists for this schema.")
    return exc
