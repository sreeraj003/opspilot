"""Alembic environment for the OpsPilot AI application database.

The database URL comes from application settings (DATABASE_URL), never from
alembic.ini, so credentials stay in the environment. Models registered on
``Base.metadata`` are the autogenerate target; Module 01 defines none yet.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine, pool

from app.core.config import get_settings
from app.database.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    settings = get_settings()
    if settings.database_url is None:
        raise RuntimeError("DATABASE_URL is not set; cannot run migrations")
    return settings.database_url.get_secret_value()


def _configure(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
