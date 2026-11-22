"""PostgreSQL connection, through SQLAlchemy's async engine and the asyncpg driver.

The database is optional: without DATABASE_URL the app works exactly as before, it just doesn't save
analyses or serve cached results.
"""

import logging
from typing import Any

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger("forkcast.db")

_engine: AsyncEngine | None = None
_sessions: async_sessionmaker[AsyncSession] | None = None

# libpq URL options (as Neon and most hosts give them) that asyncpg doesn't accept as-is.
_SSL_MODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}


def to_async_url(url: str) -> tuple[URL, dict[str, Any]]:
    """Turns a plain postgres:// URL into an asyncpg one, plus the connect arguments it needs.

    "postgresql://u:p@host/db?sslmode=require&channel_binding=require" becomes
    "postgresql+asyncpg://u:p@host/db" with connect_args {"ssl": "require"}.
    """
    parsed = make_url(url.replace("postgres://", "postgresql://", 1) if url.startswith("postgres://") else url)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError(f"Expected a PostgreSQL URL, got {parsed.drivername!r}")

    query = dict(parsed.query)
    connect_args: dict[str, Any] = {}
    sslmode = query.pop("sslmode", None)
    if sslmode in _SSL_MODES:
        connect_args["ssl"] = sslmode
    query.pop("channel_binding", None)  # asyncpg can't require channel binding; TLS still applies
    return parsed.set(drivername="postgresql+asyncpg", query=query), connect_args


def configure(url: str | None, **engine_options: Any) -> None:
    """Creates the engine for `url`, or turns the database off when `url` is empty."""
    global _engine, _sessions
    if not url:
        _engine, _sessions = None, None
        return
    async_url, connect_args = to_async_url(url)
    options = {"pool_pre_ping": True, **engine_options}  # Neon closes idle connections when it scales to zero
    if options.get("poolclass") is None:
        options.pop("poolclass", None)
        options.setdefault("pool_size", 5)
    _engine = create_async_engine(async_url, connect_args=connect_args, **options)
    _sessions = async_sessionmaker(_engine, expire_on_commit=False)
    logger.info("Database configured: %s", async_url.render_as_string(hide_password=True))


async def dispose() -> None:
    global _engine, _sessions
    if _engine is not None:
        await _engine.dispose()
    _engine, _sessions = None, None


def enabled() -> bool:
    return _sessions is not None


def session() -> AsyncSession:
    if _sessions is None:
        raise RuntimeError("The database isn't configured")
    return _sessions()
