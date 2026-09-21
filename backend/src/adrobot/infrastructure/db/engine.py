"""The engine and the sessionmaker of one process, built by whoever composes it.

The mirror image of `infrastructure/keitaro/transport.py`: `database(settings)` owns the
pool for the life of a caller's block, the way `keitaro_transport(settings)` owns the
tracker's client, so the resource's lifetime lives in the module that knows what the
resource is and 6.6 composes the two side by side.

**No module-level engine, no module-level sessionmaker, no cached getter**, for the reason
`settings.py` gives about `get_settings()` and for a mechanical one on top of it: a pooled
asyncpg connection belongs to the event loop that opened it, and measured, an engine reused
from a second `asyncio.run` hands that loop the first loop's connection and raises
`RuntimeError: Task ... got Future <Future pending> attached to a different loop` — which is
what an import-time engine would do to a test suite whose runner is not this process's first.

The DSN goes straight into the call and is never bound to a name: measured, pydantic's
`PostgresDsn` prints the password in clear from both `str()` and `repr()`, SQLAlchemy's own
`URL` masks it, and pytest runs with `--showlocals`.

A line for the README rather than a setting: behind pgbouncer in transaction mode asyncpg
needs `statement_cache_size=0`, because a session-scoped prepared statement does not survive
a connection being handed to another client. This project deploys its own PostgreSQL.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Final

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from adrobot.settings import Settings

POOL_SIZE: Final = 5
MAX_OVERFLOW: Final = 5
"""Ten connections at full stretch, and the arithmetic is the decision. The ceiling on server
connections is `uvicorn workers x (pool_size + max_overflow)`; the Dockerfile names no
`--workers`, so it is 10 of the 97 non-superuser slots this cluster has. Ten is enough because
the transaction is short, not the other way round: measured, 30 concurrent pushes against a
tracker stalled for two seconds spend **4.9 connection-seconds** in three phases and reach a
peak of 9 checked out, failing none — while the same 30 with one transaction wrapped around
the call spend **117 connection-seconds** and fail 2 of 30 on the pool. Raising the overflow
buys headroom only for the shape AGENTS.md forbids."""

POOL_TIMEOUT_SECONDS: Final = 5.0
"""How long a request waits for a connection before failing. Measured, a checkout on a
healthy pool is under a millisecond, so the default of 30 is not patience: it is a client
that gave up half a minute ago still holding a task and a tracker slot."""

POOL_RECYCLE_SECONDS: Final = 1800
"""Replace a connection rather than hand it out again after half an hour. The one constant
here that is reasoned and not measured: `pool_pre_ping` catches a peer that closed politely,
not one that stopped answering, and this cluster's `tcp_keepalives_idle` is 7200 seconds —
longer than the idle timer of any middlebox worth naming."""

CONNECT_TIMEOUT_SECONDS: Final = 3.0
"""asyncpg has no connect timeout of its own. Measured against an address that drops packets,
the first connection is still trying after two minutes — the kernel's `tcp_syn_retries` — and
the operator sees nothing; with this it fails in three seconds, the same three the transport
gives the tracker."""

APPLICATION_NAME: Final = "adrobot"
"""What this service calls itself in `pg_stat_activity`. Measured, a connection that does not
set it shows an empty string, so the one question worth asking of a backend stuck `idle in
transaction` — whose is it — has no answer."""

LOCK_TIMEOUT: Final = "3s"
"""How long a statement waits for a row lock, `StreamRepository.lock()` being the one
contention point in this design. Below `POOL_TIMEOUT_SECONDS` on purpose: a waiter that
outlived the pool's own patience would convert one wedged `FOR UPDATE` into pool timeouts on
unrelated requests before it converted into the error that names the real cause. Measured, a
short hold is waited out and a wedged one is refused with SQLSTATE 55P03."""


def build_engine(settings: Settings) -> AsyncEngine:
    """Build this process's engine, which opens no connection until something asks for one.

    `statement_timeout` and `idle_in_transaction_session_timeout` are deliberately left at the
    server's `0`; the two constants that are set are in the module docstring above.
    """
    return create_async_engine(
        # Inline and never bound to a name: pydantic renders the password in clear.
        str(settings.database_url),
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_timeout=POOL_TIMEOUT_SECONDS,
        pool_recycle=POOL_RECYCLE_SECONDS,
        # Measured: after the server closes a pooled connection — a restarted container, a
        # proxy — the next block raises asyncpg's `InterfaceError` without this and succeeds
        # with it, at the cost of one `SELECT 1` per checkout.
        pool_pre_ping=True,
        connect_args={
            "timeout": CONNECT_TIMEOUT_SECONDS,
            "server_settings": {
                "application_name": APPLICATION_NAME,
                "lock_timeout": LOCK_TIMEOUT,
            },
        },
    )


def build_sessionmaker(bind: AsyncEngine | AsyncConnection) -> async_sessionmaker[AsyncSession]:
    """Decide a session's settings once, for the process and for the `db` fixtures alike.

    `expire_on_commit=False` is what makes the push's second transaction possible: with the
    default, every row read in phase 1 is expired at the commit and the first attribute
    touched after the tracker call emits SQL from outside any transaction. Its price is that
    phase 3 would otherwise read phase 1's identity map, which is exactly why every read in
    `repositories.py` carries `populate_existing=True` — the two are one decision and move
    together.

    `autoflush=False` because there is nothing to flush: `repositories.py` never calls
    `session.add`, every write there being an explicit statement carrying its own
    `ON CONFLICT`. It is a guard on the day somebody adds one, so that the INSERT is written
    where the writer put it and not where the next read happens to trigger it.

    `bind` widens to a connection for the `db` fixtures alone, and `create_savepoint` with it:
    bound to a connection already inside a transaction, the unit of work's `COMMIT` becomes a
    `RELEASE SAVEPOINT`, so a test exercises a real commit and the fixture's rollback still
    takes it back. Spelled out because the default, `conditional_savepoint`, degrades to
    `rollback_only` there — measured, a block that raises then takes the *test's own*
    transaction down with it, after which the teardown rollback is a no-op and every later
    commit is silently dropped. Against an engine the mode never applies.
    """
    return async_sessionmaker(
        bind,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )


@asynccontextmanager
async def database(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Hold the pool open for the life of a caller's block, and dispose of it afterwards.

    Nothing is opened here — `create_async_engine` connects lazily — so a database that is
    down is a failing request rather than a service that will not start. Disposing is not
    optional: measured, an engine merely dropped leaves its connections to the garbage
    collector, which terminates them one `SAWarning` at a time, and under
    `filterwarnings = ["error"]` that warning fails whichever test is running when the
    collector fires rather than the one that leaked.
    """
    engine = build_engine(settings)
    try:
        yield build_sessionmaker(engine)
    finally:
        await engine.dispose()
