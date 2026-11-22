"""Alembic migrations for Forkcast, run through the async (asyncpg) engine.

    alembic upgrade head

The URL is DATABASE_URL_UNPOOLED when set (Neon recommends a direct, non-pooled connection for
schema changes), otherwise DATABASE_URL. Both are read from the environment or the local .env file.
Code can pass a URL instead with `config.attributes["database_url"]` (the tests do).
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import to_async_url
from app.models import Base

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    url = config.attributes.get("database_url")
    if not url:
        load_dotenv()
        url = os.getenv("DATABASE_URL_UNPOOLED") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("Set DATABASE_URL (or DATABASE_URL_UNPOOLED) to run migrations.")
    return url


def run_migrations_offline() -> None:
    """Prints the SQL instead of running it: alembic upgrade head --sql"""
    url, _ = to_async_url(database_url())
    context.configure(
        url=url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url, connect_args = to_async_url(database_url())
    engine = create_async_engine(url, connect_args=connect_args, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
