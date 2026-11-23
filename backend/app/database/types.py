"""Portable column types and mixins shared by ORM models."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Dialect, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.utils.time import utc_now


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC timestamps on every backend.

    PostgreSQL stores ``timestamptz``. SQLite (used only for fast local tests)
    has no time zone support, so values are normalized to UTC on write and
    re-attached to UTC on read; naive datetimes are rejected.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetimes are not allowed; use timezone-aware UTC values")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False)
