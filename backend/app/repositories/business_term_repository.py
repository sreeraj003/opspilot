"""Business terms and question-understanding events (Module 09)."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Text, cast, func, or_, select

from app.models.business_term import BusinessTerm, QuestionUnderstandingEvent
from app.models.schema_registry import SchemaSource
from app.repositories.base_repository import BaseRepository, Page, validate_pagination


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True, slots=True)
class TermQuery:
    schema_source_id: uuid.UUID | None = None
    search: str | None = None
    kind: str | None = None
    is_active: bool | None = None


class BusinessTermRepository(BaseRepository[BusinessTerm]):
    model = BusinessTerm
    default_order_by = (func.lower(BusinessTerm.name), BusinessTerm.id)

    async def page(self, query: TermQuery, *, offset: int, limit: int) -> Page[tuple[BusinessTerm, str | None]]:
        validate_pagination(offset, limit)
        statement = (
            select(BusinessTerm, SchemaSource.name)
            .join(SchemaSource, SchemaSource.id == BusinessTerm.schema_source_id)
            .where(BusinessTerm.deleted_at.is_(None), SchemaSource.deleted_at.is_(None))
        )
        if query.schema_source_id is not None:
            statement = statement.where(BusinessTerm.schema_source_id == query.schema_source_id)
        if query.kind is not None:
            statement = statement.where(BusinessTerm.kind == query.kind)
        if query.is_active is not None:
            statement = statement.where(BusinessTerm.is_active.is_(query.is_active))
        if query.search:
            pattern = f"%{_escape_like(query.search.lower())}%"
            statement = statement.where(
                or_(
                    func.lower(BusinessTerm.name).like(pattern, escape="\\"),
                    func.lower(BusinessTerm.description).like(pattern, escape="\\"),
                    func.lower(cast(BusinessTerm.synonyms, Text)).like(pattern, escape="\\"),
                )
            )
        total = int(await self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        rows = await self.session.execute(
            statement.order_by(func.lower(BusinessTerm.name), BusinessTerm.id).offset(offset).limit(limit)
        )
        items = [(term, source_name) for term, source_name in rows.all()]
        return Page(items=items, total=total, offset=offset, limit=limit)

    async def for_source(self, schema_source_id: uuid.UUID, *, active_only: bool = False) -> Sequence[BusinessTerm]:
        statement = self._select().where(BusinessTerm.schema_source_id == schema_source_id)
        if active_only:
            statement = statement.where(BusinessTerm.is_active.is_(True))
        return (await self.session.execute(self._ordered(statement))).scalars().all()


class QuestionUnderstandingEventRepository(BaseRepository[QuestionUnderstandingEvent]):
    model = QuestionUnderstandingEvent
    default_order_by = (QuestionUnderstandingEvent.created_at.desc(), QuestionUnderstandingEvent.id)
