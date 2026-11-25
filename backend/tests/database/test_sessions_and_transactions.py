"""Session lifecycle and transaction boundaries against PostgreSQL."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db_session
from app.database.session import Database
from app.database.transaction import transaction, transaction_depth
from app.repositories.application_setting_repository import ApplicationSettingRepository
from tests.conftest import make_settings

pytestmark = pytest.mark.postgres


@pytest.fixture
async def database(migrated_database: str, session_factory: async_sessionmaker[AsyncSession]) -> Database:
    # session_factory is requested so its teardown empties the tables after the test.
    database = Database.from_settings(make_settings(database_url=migrated_database))
    yield database  # type: ignore[misc]
    await database.dispose()


async def _count_settings(database: Database) -> int:
    async with database.session() as session:
        return await ApplicationSettingRepository(session).count()


async def test_session_is_closed_after_use(database: Database) -> None:
    async with database.session() as session:
        assert await session.scalar(text("SELECT 1")) == 1
        assert session.in_transaction()
    assert not session.in_transaction()


async def test_uncommitted_work_is_discarded_when_session_closes(database: Database) -> None:
    async with database.session() as session:
        await ApplicationSettingRepository(session).create(key="uncommitted", value=1)

    assert await _count_settings(database) == 0


async def test_session_rolls_back_on_failure(database: Database) -> None:
    with pytest.raises(RuntimeError):
        async with database.session() as session:
            await ApplicationSettingRepository(session).create(key="rolled.back", value=1)
            raise RuntimeError("boom")

    assert not session.in_transaction()
    assert await _count_settings(database) == 0


async def test_transaction_commits_on_success(database: Database) -> None:
    async with database.session() as session, transaction(session):
        await ApplicationSettingRepository(session).create(key="committed", value=1)

    assert await _count_settings(database) == 1


async def test_transaction_rolls_back_on_failure(database: Database) -> None:
    async with database.session() as session:
        with pytest.raises(RuntimeError):
            async with transaction(session):
                await ApplicationSettingRepository(session).create(key="discarded", value=1)
                raise RuntimeError("boom")
        assert transaction_depth(session) == 0

    assert await _count_settings(database) == 0


async def test_nested_transaction_joins_the_outer_one(database: Database) -> None:
    async with database.session() as session:
        repository = ApplicationSettingRepository(session)
        with pytest.raises(RuntimeError):
            async with transaction(session):
                async with transaction(session):
                    assert transaction_depth(session) == 2
                    await repository.create(key="inner.write", value=1)
                # The inner block did not commit: the outer failure discards its write too.
                raise RuntimeError("outer fails after inner succeeded")

    assert await _count_settings(database) == 0


async def test_nested_transaction_commits_once_at_the_outermost_block(database: Database) -> None:
    async with database.session() as session:
        repository = ApplicationSettingRepository(session)
        async with transaction(session):
            async with transaction(session):
                await repository.create(key="inner.ok", value=1)
            # Visible to another session only after the outer block commits.
            assert await _count_settings(database) == 0
            await repository.create(key="outer.ok", value=2)

    assert await _count_settings(database) == 2


async def test_request_dependency_yields_independent_sessions(database: Database) -> None:
    first_dependency = get_db_session(database)
    second_dependency = get_db_session(database)
    first = await anext(first_dependency)
    second = await anext(second_dependency)

    assert first is not second
    await ApplicationSettingRepository(first).create(key="only.in.first", value=1)
    assert await ApplicationSettingRepository(second).get_by_key("only.in.first") is None

    for dependency in (first_dependency, second_dependency):
        with pytest.raises(StopAsyncIteration):
            await anext(dependency)
    assert not first.in_transaction() and not second.in_transaction()
    assert await _count_settings(database) == 0


async def test_request_dependency_rolls_back_when_the_request_fails(database: Database) -> None:
    dependency = get_db_session(database)
    session = await anext(dependency)
    await ApplicationSettingRepository(session).create(key="failed.request", value=1)

    with pytest.raises(RuntimeError):
        await dependency.athrow(RuntimeError("handler failed"))

    assert not session.in_transaction()
    assert await _count_settings(database) == 0


async def test_connections_are_returned_to_the_pool(database: Database) -> None:
    for _ in range(3):
        async with database.session() as session:
            await session.scalar(text("SELECT 1"))

    assert database.engine.pool.checkedout() == 0  # type: ignore[attr-defined]
