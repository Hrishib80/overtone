"""Alembic environment.

The database URL comes from application settings rather than alembic.ini, so
migrations and the running app can never disagree about which database they
are pointed at.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import every module that defines tables so `Base.metadata` is complete before
# autogenerate compares it against the database.
import backend.database  # noqa: F401
from alembic import context
from backend.config import settings
from backend.database import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if not settings.database_url:
    raise RuntimeError("DATABASE_URL is not set; migrations need a target database.")

config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def _render_item(type_, obj, autogen_context):
    """Render custom column types as the plain SQLAlchemy type they wrap.

    Migrations must not import application code: this file will still be run
    years from now, long after `UTCDateTime` may have been renamed or removed,
    and a migration that imports a model module breaks the moment that happens.
    """
    if type_ == "type" and obj.__class__.__name__ == "UTCDateTime":
        autogen_context.imports.add("import sqlalchemy as sa")
        return "sa.DateTime(timezone=True)"
    return False


def _configure(connection: Connection | None = None, **kwargs) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_item=_render_item,
        render_as_batch=settings.database_url.startswith("sqlite"),
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(
        url=settings.database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
