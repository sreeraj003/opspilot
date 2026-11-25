"""Generic async repository for single-model persistence.

A repository wraps one ``AsyncSession`` (owned by the caller) and one model. It
provides the common operations; specialised queries go in subclasses as plain
methods using SQLAlchemy 2.x ``select()``.

Conventions:

* Repositories **never commit**. Writes ``flush`` so constraint violations
  (``IntegrityError``) surface at the call site, and the service decides when
  to commit (see ``app.database.transaction``).
* Database exceptions are never swallowed or translated here; services map the
  ones they expect (for example a unique violation to a 409).
* A missing row is ``None`` (``get``) or ``False`` (``exists``). Raising
  ``NotFoundError`` is the service's decision.
* Lists are always bounded: ``limit`` is required to be between 1 and
  ``MAX_PAGE_SIZE``.
* Models using ``SoftDeleteMixin`` are filtered to non-deleted rows unless
  ``include_deleted=True``; ``delete`` marks them deleted instead of removing them.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from app.models.base_model import SoftDeleteMixin

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100

# Managed by mixins and the database; never set through ``update``.
_PROTECTED_FIELDS = frozenset({"id", "created_at", "updated_at", "deleted_at"})


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: Sequence[T]
    total: int
    offset: int
    limit: int


def validate_pagination(offset: int, limit: int) -> None:
    if offset < 0:
        raise ValueError("offset must be 0 or greater")
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")


class BaseRepository[ModelT]:
    model: ClassVar[type[Any]]
    # Stable ordering for lists and pages. Subclasses override with an indexed column.
    default_order_by: ClassVar[tuple[InstrumentedAttribute[Any] | ColumnElement[Any], ...]] = ()

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def _soft_delete(self) -> bool:
        return issubclass(self.model, SoftDeleteMixin)

    def _select(self, *, include_deleted: bool = False) -> Select[tuple[ModelT]]:
        statement: Select[tuple[ModelT]] = select(self.model)
        if self._soft_delete and not include_deleted:
            statement = statement.where(self.model.deleted_at.is_(None))
        return statement

    def _ordered(self, statement: Select[tuple[ModelT]]) -> Select[tuple[ModelT]]:
        order_by = self.default_order_by or (self.model.id,)
        return statement.order_by(*order_by)

    # --- reads -------------------------------------------------------------------

    async def get(self, entity_id: uuid.UUID, *, include_deleted: bool = False) -> ModelT | None:
        statement = self._select(include_deleted=include_deleted).where(self.model.id == entity_id)
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list(
        self, *, offset: int = 0, limit: int = DEFAULT_PAGE_SIZE, include_deleted: bool = False
    ) -> Sequence[ModelT]:
        validate_pagination(offset, limit)
        statement = self._ordered(self._select(include_deleted=include_deleted)).offset(offset).limit(limit)
        return (await self.session.execute(statement)).scalars().all()

    async def count(self, *, include_deleted: bool = False) -> int:
        subquery = self._select(include_deleted=include_deleted).subquery()
        return int(await self.session.scalar(select(func.count()).select_from(subquery)) or 0)

    async def exists(self, entity_id: uuid.UUID, *, include_deleted: bool = False) -> bool:
        statement = self._select(include_deleted=include_deleted).where(self.model.id == entity_id)
        return bool(await self.session.scalar(select(statement.exists())))

    async def paginate(
        self, *, offset: int = 0, limit: int = DEFAULT_PAGE_SIZE, include_deleted: bool = False
    ) -> Page[ModelT]:
        items = await self.list(offset=offset, limit=limit, include_deleted=include_deleted)
        total = await self.count(include_deleted=include_deleted)
        return Page(items=items, total=total, offset=offset, limit=limit)

    # --- writes (flush, never commit) ------------------------------------------------

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def create(self, **values: Any) -> ModelT:
        return await self.add(self.model(**values))

    async def update(self, entity: ModelT, values: Mapping[str, Any]) -> ModelT:
        columns = self.model.__mapper__.columns.keys()
        for field, value in values.items():
            if field in _PROTECTED_FIELDS or field not in columns:
                raise ValueError(f"{self.model.__name__}.{field} cannot be updated")
            setattr(entity, field, value)
        await self.session.flush()
        return entity

    async def delete(self, entity: ModelT) -> None:
        """Soft-delete models with ``SoftDeleteMixin``; remove all others."""
        if isinstance(entity, SoftDeleteMixin):
            entity.mark_deleted()
            await self.session.flush()
        else:
            await self.hard_delete(entity)

    async def hard_delete(self, entity: ModelT) -> None:
        await self.session.delete(entity)
        await self.session.flush()
