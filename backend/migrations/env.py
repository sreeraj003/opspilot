"""Alembic environment.

The database URL comes from application settings (DATABASE_URL), never from
alembic.ini, so credentials stay in the environment. A caller may also pass a
connection via ``config.attributes["connection"]`` (used by tests).
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.config import get_settings
from app.database.base import Base

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    url = config.attributes.get("database_url")
    if url:
        return url
    settings = get_settings()
    if settings.database_url is None:
        raise RuntimeError("DATABASE_URL is not set; cannot run migrations")
    return settings.database_url.get_secret_value()


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )


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
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = engine_from_config({"sqlalchemy.url": _database_url()}, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
