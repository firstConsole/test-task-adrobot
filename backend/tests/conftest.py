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

from adrobot.api.app import create_app
from adrobot.logging import configure_logging
from adrobot.settings import Settings
from tests.helpers import ENV_PREFIX, VALID_ENVIRONMENT

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from fastapi import FastAPI


@pytest.fixture
def anyio_backend() -> str:
    """Pin the event loop backend instead of inheriting anyio's parametrised fixture.

    anyio's own `anyio_backend` is `params=get_available_backends()`. With only asyncio
    installed that is a one-element parametrisation today, but the day anything pulls trio
    in transitively the suite silently doubles and the halves that touch asyncpg and
    `asyncio.*` start failing. This project runs on asyncio, so it says so.

    Function-scoped, where anyio's is module-scoped: the first module-scoped async fixture
    (the stage-5 engine, most likely) will need this widened, and the ScopeMismatch that
    announces it is loud and one word to fix.
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
def app(settings: Settings) -> FastAPI:
    """The application under test, built from the fixture settings."""
    return create_app(settings=settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client that speaks to the application in-process.

    `httpx.ASGITransport`, not `fastapi.testclient.TestClient`: 1.4 set
    `filterwarnings = ["error"]`, and importing TestClient trips a starlette deprecation
    under httpx 0.x — a *collection* error, which aborts the whole run rather than one
    test.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://adrobot.test") as http:
        yield http


# Deliberately not here yet, and which stage brings it:
#
#   6.x   fixtures over tests/fakes.py. The fakes themselves arrived at 4.8; a fixture
#         for one belongs in the commit that brings the first scenario to build on it,
#         because what a scenario wants configured is not knowable before there is one.
#   6.6   a `client` that runs the lifespan. ASGITransport does not run one, and there is
#         still none to run: 4.3 gave the tracker client its own context manager instead
#         of an application lifespan. The day create_app takes a ports factory, this
#         becomes `async with app.router.lifespan_context(app): yield http`.
#   5.1   a `db`-marked engine/session fixture that skips with a visible reason when
#         Postgres is unreachable, and rolls back a transaction per test.
#   6.8   an `authorised_client` carrying the shared token.
