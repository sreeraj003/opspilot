"""Fixtures for tests that talk to the disposable test database.

* ``engine``: one async engine for the whole run, on a database migrated to head.
* ``db_session``: a fresh ``AsyncSession`` per test; every table is emptied afterwards.
* ``Widget``: a test-only model on its own metadata (not part of the application
  schema) that combines all mixins, including soft delete, to exercise them.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import MetaData, String, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.authorization.system_catalog import ensure_system_rbac
from app.database.base import TYPE_ANNOTATION_MAP, Base
from app.database.naming import NAMING_CONVENTION
from app.database.session import create_engine_from_settings
from app.models.base_model import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.repositories.base_repository import BaseRepository
from tests.conftest import make_settings

pytestmark = pytest.mark.postgres


class _TestOnlyBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = TYPE_ANNOTATION_MAP
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012


class Widget(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, _TestOnlyBase):
    __tablename__ = "test_widgets"

    name: Mapped[str] = mapped_column(String(64), unique=True)


class WidgetRepository(BaseRepository[Widget]):
    model = Widget
    default_order_by = (Widget.name,)


def _all_tables() -> list[str]:
    return [*Base.metadata.tables, *_TestOnlyBase.metadata.tables]


@pytest.fixture(scope="session")
async def engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine_from_settings(make_settings(database_url=migrated_database))
    async with engine.begin() as connection:
        await connection.run_sync(_TestOnlyBase.metadata.drop_all)
        await connection.run_sync(_TestOnlyBase.metadata.create_all)
    yield engine
    async with engine.begin() as connection:
        await connection.run_sync(_TestOnlyBase.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE " + ", ".join(_all_tables())))
    # Restore the built-in permissions and Administrator role that migration 0003 seeds.
    async with factory() as session:
        await ensure_system_rbac(session)


@pytest.fixture
async def db_session(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
