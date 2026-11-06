"""Async engine creation for SQLite (default), PostgreSQL and MySQL."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.db.models import Base

SUPPORTED_DRIVERS = {
    "sqlite+aiosqlite": "SQLite",
    "postgresql+asyncpg": "PostgreSQL",
    "mysql+aiomysql": "MySQL",
    "mariadb+aiomysql": "MariaDB",
}


def normalise_database_url(url: str) -> str:
    """Accept common shorthand URLs and map them to the async drivers."""

    parsed = make_url(url)
    driver = parsed.drivername
    mapping = {
        "sqlite": "sqlite+aiosqlite",
        "postgres": "postgresql+asyncpg",
        "postgresql": "postgresql+asyncpg",
        "mysql": "mysql+aiomysql",
        "mariadb": "mariadb+aiomysql",
    }
    if driver in mapping:
        parsed = parsed.set(drivername=mapping[driver])
    if parsed.drivername not in SUPPORTED_DRIVERS:
        raise ValueError(
            f"Unsupported DATABASE_URL driver {parsed.drivername!r}; use one of: {', '.join(SUPPORTED_DRIVERS)}"
        )
    if parsed.drivername.endswith("aiomysql") and "charset" not in parsed.query:
        parsed = parsed.update_query_dict({"charset": "utf8mb4"})
    return parsed.render_as_string(hide_password=False)


def describe_database(url: str) -> str:
    parsed = make_url(url)
    name = SUPPORTED_DRIVERS.get(parsed.drivername, parsed.drivername)
    if parsed.drivername.startswith("sqlite"):
        return f"{name} ({parsed.database})"
    return f"{name} ({parsed.host}:{parsed.port or 'default'}/{parsed.database})"


def create_engine(url: str) -> AsyncEngine:
    url = normalise_database_url(url)
    parsed = make_url(url)
    if parsed.drivername.startswith("sqlite"):
        if parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_async_engine(url, connect_args={"timeout": 30})

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record) -> None:  # pragma: no cover - driver callback
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        return engine

    return create_async_engine(url, pool_pre_ping=True, pool_recycle=1800, pool_size=5, max_overflow=5)


async def init_models(engine: AsyncEngine) -> None:
    """Create missing tables. Schema changes are additive in 2.x releases."""

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
