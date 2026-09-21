"""Root fixtures for the whole suite.

Deliberately small: everything here is used by a test that exists today. What is not here
yet, and which stage brings it, is listed at the foot of the file.
"""

from __future__ import annotations

import logging
import os
from io import StringIO
from typing import TYPE_CHECKING

import httpx
import pytest
import structlog
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError

from adrobot.api.app import create_app
from adrobot.infrastructure.db.engine import build_engine, build_sessionmaker
from adrobot.infrastructure.db.uow import unit_of_work
from adrobot.logging import configure_logging
from adrobot.settings import Settings
from tests.helpers import (
    DATABASE_URL_VARIABLE,
    ENV_PREFIX,
    LOCAL_DATABASE_URL,
    ROWS_PER_TABLE,
    TRUNCATE_EVERY_TABLE,
    VALID_ENVIRONMENT,
    Statements,
)
from tests.wiring import FakeWorld, fake_ports_factory, fake_world

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import (
        AsyncConnection,
        AsyncEngine,
        AsyncSession,
        async_sessionmaker,
    )

    from adrobot.application.ports.persistence import UnitOfWork


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Pin the event loop backend instead of inheriting anyio's parametrised fixture.

    anyio's own `anyio_backend` is `params=get_available_backends()`. With only asyncio
    installed that is a one-element parametrisation today, but the day anything pulls trio
    in transitively the suite silently doubles and the halves that touch asyncpg and
    `asyncio.*` start failing. This project runs on asyncio, so it says so.

    Session-scoped, where anyio's is module-scoped, and wider than the prediction this
    docstring used to carry: the `postgres` engine below is a session-scoped async fixture,
    and anyio keeps one event loop alive for exactly as long as an async fixture holds its
    runner. Narrower than the engine and the answer is a ScopeMismatch; wider than the engine
    and a pooled asyncpg connection outlives the loop that opened it.
    """
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every `ADROBOT_*` variable the developer's shell happens to export.

    Load-bearing rather than tidy. `Settings` reads the real process environment and
    rejects a prefixed name it does not know, so one stale variable in somebody's `.envrc`
    is the difference between a suite that is green in CI and red on their machine.
    """
    for name in list(os.environ):
        if name.casefold().startswith(ENV_PREFIX.casefold()):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _isolated_logging() -> Iterator[None]:
    """Put structlog and the root logger back the way the test found them.

    `configure_logging` is a process-wide side effect: without this, the first test to
    call it decides where every later test's records go, and a test that asserts on an
    empty buffer passes for the wrong reason.
    """
    handlers = logging.getLogger().handlers[:]
    level = logging.getLogger().level
    yield
    structlog.reset_defaults()
    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture
def valid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Export the one canonical working environment."""
    for name, value in VALID_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def settings(valid_environment: None) -> Settings:
    """A `Settings` built the way the process builds it: from the environment, no arguments.

    A test that needs a variant asks for `valid_environment`, overrides the one variable it
    cares about and calls `Settings()` itself. Parsing the environment is the behaviour
    under test, so nothing may route around it.
    """
    return Settings()


@pytest.fixture
def log_stream() -> StringIO:
    """Install the real processor chain with its output pointed at a buffer.

    Not `structlog.testing.capture_logs`: that replaces the chain with a single capturing
    processor, so the correlation-id stamp, the redactor and the renderer — the three
    things worth asserting — never run, and the tests pass without testing anything.

    No teardown of its own; the autouse `_isolated_logging` above restores the globals,
    and ruff PT022 is right that a `yield` with nothing after it is a lie about a fixture.
    """
    buffer = StringIO()
    configure_logging(level="DEBUG", renderer="json", stream=buffer)
    return buffer


@pytest.fixture
def world() -> FakeWorld:
    """The fakes one application is served from, reachable by the test that drives it.

    One per test and shared by every request that test makes, which is what a pool and a
    tracker client are in production: a campaign created by one request is there for the
    next one to read.
    """
    return fake_world()


@pytest.fixture
def app(settings: Settings, world: FakeWorld) -> FastAPI:
    """The application under test, built from the fixture settings over the fixture fakes."""
    return create_app(settings=settings, ports_factory=fake_ports_factory(world.ports))


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client that speaks to the application in-process.

    `httpx.ASGITransport`, not `fastapi.testclient.TestClient`: 1.4 set
    `filterwarnings = ["error"]`, and importing TestClient trips a starlette deprecation
    under httpx 0.x — a *collection* error, which aborts the whole run rather than one
    test.

    The lifespan is entered by hand because `ASGITransport` is not a server and never sends
    the `lifespan` messages one does — so without this the ports would never be composed and
    every dependency that reads them would answer 500.
    """
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://adrobot.test") as http,
    ):
        yield http


@pytest.fixture(scope="session")
def database_settings() -> Settings:
    """`Settings` for the cluster the suite talks to, built the one way the process builds one.

    Exactly one variable is overridden: `VALID_ENVIRONMENT` names host `db`, the compose
    service, which resolves nowhere else. `MonkeyPatch.context()` and not the `monkeypatch`
    fixture, which is function-scoped — the block closes before this returns, so nothing of it
    leaks into a test that asserts on the environment.
    """
    with pytest.MonkeyPatch.context() as environment:
        for name, value in VALID_ENVIRONMENT.items():
            environment.setenv(name, value)
        environment.setenv(
            ENV_PREFIX + "DATABASE_URL",
            os.environ.get(DATABASE_URL_VARIABLE, LOCAL_DATABASE_URL),
        )
        return Settings()


@pytest.fixture(scope="session")
async def postgres(database_settings: Settings) -> AsyncIterator[AsyncEngine]:
    """The one engine of the run, or the reason there is none.

    Probed through `build_engine` rather than through a socket check of its own, so an
    unreachable address costs the suite exactly what it costs a request — the same
    three-second connect budget — once. A skip would hide a service container that never came
    up, so on CI the same verdict is a failure. The URL is rendered through SQLAlchemy, which
    masks the password; pydantic's own `str()` prints it.
    """
    engine = build_engine(database_settings)
    try:
        async with engine.connect() as probe:
            await probe.execute(text("SELECT 1"))
    except (OSError, SQLAlchemyError) as exc:
        await engine.dispose()
        unreachable = (
            f"PostgreSQL is unreachable at {engine.url.render_as_string(hide_password=True)}: "
            f"{type(exc).__name__}. Start it with `make up`, or point "
            f"{DATABASE_URL_VARIABLE} at another cluster."
        )
        if os.environ.get("CI"):
            pytest.fail(unreachable, pytrace=False)
        pytest.skip(unreachable)
    try:
        yield engine
    finally:
        async with engine.connect() as census:
            left = {
                relation: rows
                for relation, rows in (await census.execute(text(ROWS_PER_TABLE))).all()
                if rows
            }
        await engine.dispose()
    # The one check no single test can make: `db` rolls its own test back and
    # `pooled_sessions` sweeps after itself, but a test that builds a sessionmaker off
    # `postgres` by hand commits for real and nothing else would notice.
    assert not left, f"the suite left rows behind: {left}"


@pytest.fixture
async def db(postgres: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """One connection inside one transaction that is always rolled back.

    A connection and not a session, because `SELECT count(*)` and `EXPLAIN` are assertions
    about the database rather than about a repository, and because everything below is built
    on it. What is load-bearing is `connection.begin()`: without it the session joins nothing,
    its commit is a real COMMIT, and the row is still there in the next test.
    """
    async with postgres.connect() as connection:
        outer = await connection.begin()
        try:
            yield connection
        finally:
            await outer.rollback()


@pytest.fixture
def sessions(db: AsyncConnection) -> async_sessionmaker[AsyncSession]:
    """Sessions on the rolled-back connection, where a real COMMIT becomes a RELEASE SAVEPOINT.

    `build_sessionmaker` and not a second one written here: `join_transaction_mode` is set
    there precisely so that this binding works.
    """
    return build_sessionmaker(db)


@pytest.fixture
async def uow(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[UnitOfWork]:
    """The real unit of work over the rolled-back connection: one per test, as one per request.

    `unit_of_work()` and not `SqlAlchemyUnitOfWork(session)`, so that `autobegin=False` — the
    flag that makes a `Transaction` kept past its block raise rather than lose its writes — is
    exercised by every database test and not only by the one that remembers it.
    """
    async with unit_of_work(sessions) as unit:
        yield unit


@pytest.fixture
async def pooled_sessions(
    postgres: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions straight off the pool, whose commits are real and visible to another connection.

    For what a rollback cannot show: a `FOR UPDATE` blocking a concurrent INSERT needs a second
    transaction, and a second transaction cannot see a row that was never committed. It pays
    with a sweep instead of a rollback, which is why these fixtures own the cluster outright.
    """
    try:
        yield build_sessionmaker(postgres)
    finally:
        async with postgres.begin() as sweep:
            await sweep.execute(text(TRUNCATE_EVERY_TABLE))


@pytest.fixture
def statements(postgres: AsyncEngine) -> Iterator[Statements]:
    """Count what the engine sends, for the tests that assert a constant number of statements.

    On the engine and not on a connection, so that `selectinload`'s follow-up queries — which
    are the whole point of the count — are seen. Removed in teardown: the engine outlives the
    test, and a listener left behind would put the next test's SQL in a dead test's list.
    """
    counted = Statements()

    def record(  # noqa: PLR0913, PLR0917
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,  # noqa: FBT001  # positional in SQLAlchemy's event signature
    ) -> None:
        counted.sent.append(statement)

    event.listen(postgres.sync_engine, "before_cursor_execute", record)
    try:
        yield counted
    finally:
        event.remove(postgres.sync_engine, "before_cursor_execute", record)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark as `db` every test whose fixture closure reaches PostgreSQL.

    Derived and never written by hand: the marker is a fact about the fixtures a test asked
    for, so a test that forgot it would still open a connection while `-m "not db"` — the fast
    loop, and any job without a service container — went on believing it had not.
    """
    for item in items:
        if "postgres" in getattr(item, "fixturenames", ()):
            item.add_marker("db")


# Deliberately not here yet, and which stage brings it:
#
#   7.x   a fixture for a campaign with a live draft. The editor is the first thing that
#         wants one, and what a draft should be seeded with is not knowable before there is
#         a scenario editing it.
#
# What used to be on this list and has arrived: the `world` of fakes above (6.6), the
# `client` that enters the lifespan by hand (6.6) and the shared token, which is a header a
# test module sets on the client it was handed rather than a second client fixture — one
# line where a fixture would have been a second thing to keep in step with `Settings`.
