"""BaseRepository operations against PostgreSQL."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.transaction import transaction
from app.models import ApplicationSetting
from app.repositories.application_setting_repository import ApplicationSettingRepository
from app.repositories.base_repository import MAX_PAGE_SIZE

pytestmark = pytest.mark.postgres


async def test_create_and_get_by_id(db_session: AsyncSession) -> None:
    repository = ApplicationSettingRepository(db_session)

    created = await repository.create(key="ui.theme", value="dark", description="Default theme")
    await db_session.commit()
    db_session.expunge_all()

    loaded = await repository.get(created.id)
    assert loaded is not None
    assert (loaded.key, loaded.value, loaded.description) == ("ui.theme", "dark", "Default theme")


async def test_get_missing_returns_none_and_exists_is_false(db_session: AsyncSession) -> None:
    repository = ApplicationSettingRepository(db_session)

    assert await repository.get(uuid.uuid4()) is None
    assert await repository.get_by_key("does.not.exist") is None
    assert await repository.exists(uuid.uuid4()) is False


async def test_update_changes_columns(db_session: AsyncSession) -> None:
    repository = ApplicationSettingRepository(db_session)
    setting = await repository.create(key="ui.page_size", value=25)

    await repository.update(setting, {"value": 50, "description": "Rows per page"})
    await db_session.commit()
    db_session.expunge_all()

    reloaded = await repository.get_by_key("ui.page_size")
    assert reloaded is not None
    assert (reloaded.value, reloaded.description) == (50, "Rows per page")


@pytest.mark.parametrize("field", ["id", "created_at", "updated_at", "not_a_column"])
async def test_update_rejects_protected_or_unknown_fields(db_session: AsyncSession, field: str) -> None:
    repository = ApplicationSettingRepository(db_session)
    setting = await repository.create(key="ui.locked", value=1)

    with pytest.raises(ValueError, match="cannot be updated"):
        await repository.update(setting, {field: None})


async def test_delete_removes_row(db_session: AsyncSession) -> None:
    repository = ApplicationSettingRepository(db_session)
    setting = await repository.create(key="ui.temporary", value=True)

    await repository.delete(setting)
    await db_session.commit()

    assert await repository.get(setting.id) is None
    assert await repository.count() == 0


async def test_count_exists_and_pagination(db_session: AsyncSession) -> None:
    repository = ApplicationSettingRepository(db_session)
    for index in range(7):
        await repository.create(key=f"page.item_{index}", value=index)
    await db_session.commit()

    first = await repository.paginate(offset=0, limit=3)
    last = await repository.paginate(offset=6, limit=3)
    beyond = await repository.paginate(offset=20, limit=3)

    assert await repository.count() == 7
    assert first.total == 7 and (first.offset, first.limit) == (0, 3)
    assert [s.key for s in first.items] == ["page.item_0", "page.item_1", "page.item_2"]
    assert [s.key for s in last.items] == ["page.item_6"]
    assert beyond.items == [] and beyond.total == 7
    assert await repository.exists(first.items[0].id)


@pytest.mark.parametrize(("offset", "limit"), [(-1, 10), (0, 0), (0, MAX_PAGE_SIZE + 1)])
async def test_lists_are_always_bounded(db_session: AsyncSession, offset: int, limit: int) -> None:
    with pytest.raises(ValueError):
        await ApplicationSettingRepository(db_session).list(offset=offset, limit=limit)


async def test_duplicate_key_is_rejected_by_the_database(db_session: AsyncSession) -> None:
    repository = ApplicationSettingRepository(db_session)
    await repository.create(key="dup.key", value=1)

    with pytest.raises(IntegrityError) as caught:
        await repository.create(key="dup.key", value=2)

    assert caught.value.orig.sqlstate == "23505"  # type: ignore[union-attr]
    assert "uq_application_settings_key" in str(caught.value.orig)


@pytest.mark.parametrize(
    "values",
    [
        {"key": "Upper.Case", "value": 1},
        {"key": "1starts.with.digit", "value": 1},
        {"key": "ok.key", "value": 1, "description": "x" * 1001},
    ],
)
async def test_check_constraints_are_enforced(db_session: AsyncSession, values: dict[str, object]) -> None:
    with pytest.raises(IntegrityError) as caught:
        await ApplicationSettingRepository(db_session).create(**values)

    assert caught.value.orig.sqlstate == "23514"  # type: ignore[union-attr]


async def test_value_is_not_nullable(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError) as caught:
        await ApplicationSettingRepository(db_session).create(key="null.value", value=None)

    assert caught.value.orig.sqlstate == "23502"  # type: ignore[union-attr]  (not-null violation)


async def test_json_null_value_is_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError) as caught:
        await db_session.execute(text("INSERT INTO application_settings (key, value) VALUES ('json.null', 'null')"))

    assert "ck_application_settings_value_not_null" in str(caught.value.orig)


async def test_repository_does_not_commit(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await ApplicationSettingRepository(session).create(key="never.committed", value=1)
    # Session closed without commit: the flushed row is gone.
    async with session_factory() as session:
        assert await ApplicationSettingRepository(session).get_by_key("never.committed") is None


async def test_failed_transaction_rolls_back_every_repository_write(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = ApplicationSettingRepository(session)
        with pytest.raises(IntegrityError):
            async with transaction(session):
                await repository.create(key="first.write", value=1)
                await repository.create(key="first.write", value=2)

    async with session_factory() as session:
        assert await ApplicationSettingRepository(session).count() == 0


async def test_raw_rows_are_loaded_as_models(db_session: AsyncSession) -> None:
    db_session.add(ApplicationSetting(key="model.load", value={"a": [1, 2]}))
    await db_session.flush()

    loaded = await ApplicationSettingRepository(db_session).get_by_key("model.load")
    assert loaded is not None and loaded.value == {"a": [1, 2]}
