"""Declarative base shared by every ORM model.

All models inherit from ``Base`` so they share one ``MetaData`` (the target of
Alembic autogenerate) and one set of naming conventions and type mappings.
Reusable columns (UUID primary key, timestamps, soft delete) are mixins in
``app.models.base_model``.
"""

import uuid
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

from app.database.naming import NAMING_CONVENTION

# Python annotation -> column type. Explicit so a bare ``Mapped[datetime]`` can
# never become a naive TIMESTAMP and ``Mapped[uuid.UUID]`` is PostgreSQL's native UUID.
TYPE_ANNOTATION_MAP: dict[Any, Any] = {
    datetime: DateTime(timezone=True),
    uuid.UUID: Uuid(as_uuid=True),
    dict[str, Any]: JSONB,
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = TYPE_ANNOTATION_MAP

    # Fetch server-generated values (defaults, ``updated_at``) with RETURNING
    # right after INSERT/UPDATE, so async code never triggers an implicit lazy load.
    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": True}
