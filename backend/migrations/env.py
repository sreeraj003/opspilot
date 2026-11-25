"""Alembic environment for the OpsPilot AI application database.

* The database URL comes from application settings (DATABASE_URL), never from
  alembic.ini, so credentials stay in the environment. Programmatic callers
  (tests) may pass a URL with ``config.attributes["database_url"]`` instead.
* ``import app.models`` registers every model on ``Base.metadata``, the
  autogenerate target.
* Migrations run synchronously with the same psycopg 3 driver the application
  uses asynchronously. Migrations are a one-shot CLI step, so an async
  environment would add complexity without benefit.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine, pool

import app.models  # noqa: F401  (registers models on Base.metadata)
from app.core.config import Settings, get_settings
from app.database.base import Base

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    override = config.attributes.get("database_url")
    if override:
        # Normalised through Settings so postgresql:// URLs get the psycopg driver.
        settings = Settings(_env_file=None, database_url=override)  # type: ignore[call-arg]
    else:
        settings = get_settings()
    if settings.database_url is None:
        raise RuntimeError("DATABASE_URL is not set; cannot run migrations")
    return settings.database_url.get_secret_value()


_CONTEXT_OPTIONS = {
    "target_metadata": target_metadata,
    "compare_type": True,
    "compare_server_default": True,
}


def _configure(connection: Connection) -> None:
    context.configure(connection=connection, **_CONTEXT_OPTIONS)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_CONTEXT_OPTIONS,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        _database_url(),
        poolclass=pool.NullPool,
        connect_args={"options": "-c timezone=UTC"},
    )
    try:
        with engine.connect() as connection:
            _configure(connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
