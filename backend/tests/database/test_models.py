"""Base model behaviour against PostgreSQL: UUIDs, timestamps, soft delete."""

import uuid
from datetime import UTC, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import ApplicationSetting
from tests.database.conftest import Widget, WidgetRepository

pytestmark = pytest.mark.postgres


async def test_uuid_primary_key_is_generated(db_session: AsyncSession) -> None:
    first = Widget(name="first")
    second = Widget(name="second")
    db_session.add_all([first, second])
    await db_session.commit()

    assert isinstance(first.id, uuid.UUID)
    assert first.id.version == 4
    assert first.id != second.id


async def test_database_generates_uuid_for_rows_inserted_outside_the_orm(db_session: AsyncSession) -> None:
    await db_session.execute(text("INSERT INTO application_settings (key, value) VALUES ('raw.insert', '1')"))
    generated = await db_session.scalar(text("SELECT id FROM application_settings WHERE key = 'raw.insert'"))

    assert isinstance(generated, uuid.UUID)


async def test_timestamps_are_set_on_creation_and_timezone_aware(db_session: AsyncSession) -> None:
    widget = Widget(name="stamped")
    db_session.add(widget)
    await db_session.commit()

    assert widget.created_at is not None
    assert widget.updated_at == widget.created_at
    for value in (widget.created_at, widget.updated_at):
        assert value.tzinfo is not None
        assert value.utcoffset() == timedelta(0)
    database_now = await db_session.scalar(text("SELECT now()"))
    assert abs(database_now - widget.created_at) < timedelta(minutes=1)


async def test_updated_at_changes_on_update_and_created_at_does_not(db_session: AsyncSession) -> None:
    widget = Widget(name="before")
    db_session.add(widget)
    await db_session.commit()
    created_at, updated_at = widget.created_at, widget.updated_at

    widget.name = "after"
    await db_session.commit()

    assert widget.created_at == created_at
    assert widget.updated_at > updated_at
    assert widget.updated_at.tzinfo is not None


async def test_timestamps_survive_a_round_trip_in_utc(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        setting = ApplicationSetting(key="tz.check", value=True)
        session.add(setting)
        await session.commit()
    async with session_factory() as session:
        loaded = (await session.execute(select(ApplicationSetting).where(ApplicationSetting.key == "tz.check"))).scalar_one()

    assert loaded.created_at.tzinfo is not None
    assert loaded.created_at.astimezone(UTC) == setting.created_at.astimezone(UTC)


async def test_soft_delete_hides_rows_from_default_reads(db_session: AsyncSession) -> None:
    repository = WidgetRepository(db_session)
    kept = await repository.create(name="kept")
    removed = await repository.create(name="removed")

    await repository.delete(removed)
    await db_session.commit()

    assert removed.is_deleted
    assert removed.deleted_at is not None and removed.deleted_at.tzinfo is not None
    assert await repository.get(removed.id) is None
    assert await repository.get(removed.id, include_deleted=True) is removed
    assert [w.name for w in await repository.list()] == ["kept"]
    assert await repository.count() == 1
    assert await repository.count(include_deleted=True) == 2
    assert not await repository.exists(removed.id)
    assert await repository.exists(kept.id)
    # The row is still physically present.
    assert await db_session.scalar(text("SELECT count(*) FROM test_widgets")) == 2


async def test_soft_deleted_row_can_be_restored(db_session: AsyncSession) -> None:
    repository = WidgetRepository(db_session)
    widget = await repository.create(name="restorable")
    await repository.delete(widget)

    widget.restore()
    await db_session.flush()

    assert not widget.is_deleted
    assert await repository.get(widget.id) is widget


async def test_hard_delete_removes_soft_deletable_rows(db_session: AsyncSession) -> None:
    repository = WidgetRepository(db_session)
    widget = await repository.create(name="gone")

    await repository.hard_delete(widget)

    assert await repository.get(widget.id, include_deleted=True) is None
