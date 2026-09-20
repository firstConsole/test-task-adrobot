"""Alembic's entry point, wired to this project's async engine and its one configuration.

Two departures from `alembic init -t async`, both decisions rather than taste:

*   The database URL comes from `Settings` and not from `alembic.ini`. One name reads the
    database in this project — `ADROBOT_DATABASE_URL` — so a second copy in a committed
    ini file would be a second thing to keep in step and a place to write a password
    down. It also means a missing or misspelled variable fails here with exactly the
    message it fails with in the api container, which is the point of that guard.
*   Logging goes through `configure_logging`, not `fileConfig`. The migrator is a
    container beside the api in one stack, and a stack that emits JSON on one service and
    `INFO  [alembic]` on another cannot be ingested by anything. This is the caller
    `configure_logging` takes primitives for, rather than a `Settings`.

The engine is built with `create_async_engine` rather than `async_engine_from_config`:
the latter round-trips the URL through ConfigParser, where a `%` in a password is
interpolation syntax.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from adrobot.logging import configure_logging
from adrobot.settings import Settings

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

# Nothing to autogenerate against yet. Sub-stage 5.1 writes
# infrastructure/db/base.py and points this at `Base.metadata`; until then `--autogenerate`
# would confidently propose dropping every table it finds.
target_metadata = None

_settings = Settings()

configure_logging(
    level=_settings.log_level,
    # The same mapping main.create_asgi_app makes, and stated here rather than shared
    # because these are two composition roots: one serves HTTP, one runs migrations and
    # exits, and neither should read the other's decisions.
    renderer="console" if _settings.env == "dev" else "json",
)


def run_migrations_offline() -> None:
    """Emit the SQL for this upgrade instead of running it, for `alembic upgrade --sql`.

    Kept because PLAN-01 §4 requires every generated migration to be read before it is
    committed, and reading the SQL it compiles to is the strongest form of that.
    """
    context.configure(
        url=str(_settings.database_url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    """Run the migrations against an already-open synchronous connection."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    """Open the async engine and hand a synchronous view of it to alembic.

    Alembic's migration API is synchronous, so the async connection is bridged with
    `run_sync` rather than reimplemented. NullPool because this process opens one
    connection and exits; a pooled engine would just delay that exit.
    """
    engine = create_async_engine(str(_settings.database_url), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    """Run the migrations against the database named by ADROBOT_DATABASE_URL."""
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
