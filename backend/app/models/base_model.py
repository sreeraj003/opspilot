"""Reusable column mixins for application models.

Combine them explicitly per model; nothing is applied to every table::

    class Thing(UUIDPrimaryKeyMixin, TimestampMixin, Base):
        __tablename__ = "things"

``SoftDeleteMixin`` is opt-in: use it only where hiding a row is the business
meaning of "delete" (for example a user who must keep their history). Audit and
history tables must not use it: they are append-only and never deleted.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, text
from sqlalchemy.orm import Mapped, mapped_column


class UUIDPrimaryKeyMixin:
    """UUID v4 primary key ``id``.

    Generated in Python (so the id is known before flush) with a database
    default as well, so rows inserted outside the ORM also get one.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        sort_order=-100,
    )


class TimestampMixin:
    """``created_at`` / ``updated_at`` as timezone-aware UTC timestamps.

    Both are set by PostgreSQL (``now()``), so all rows share one clock.
    ``updated_at`` is refreshed on every ORM or Core ``UPDATE`` of the row; raw
    SQL updates must set it themselves.
    """

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=100)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), sort_order=101)


class SoftDeleteMixin:
    """Nullable ``deleted_at``: a row is deleted when it is set.

    ``BaseRepository`` excludes soft-deleted rows from every read unless
    ``include_deleted=True`` is passed, and its ``delete`` sets ``deleted_at``
    instead of removing the row. Specialised repository queries must add
    ``Model.deleted_at.is_(None)`` themselves (``BaseRepository._select`` does it).
    """

    deleted_at: Mapped[datetime | None] = mapped_column(default=None, sort_order=102)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def mark_deleted(self) -> None:
        self.deleted_at = datetime.now(UTC)

    def restore(self) -> None:
        self.deleted_at = None
