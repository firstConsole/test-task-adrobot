"""Entry point of the HTTP process.

    uvicorn adrobot.main:create_asgi_app --factory

A factory, and no module-level `app`. With `app = create_app(...)` at module scope the
settings validator runs at *import* time, so every test, every CLI command and every
`python -c "import adrobot.main"` would need a complete environment before it could do
anything, and the failure would arrive wrapped in six frames of import machinery. A
factory moves that work into a call. It is also the only shape that lets one process build
more than one application, which the docs-in-production test relies on. `tests/test_main.py`
asserts the absence of `app` so that nobody adds one back for convenience.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adrobot.api.app import create_app
from adrobot.composition import build_ports
from adrobot.logging import configure_logging
from adrobot.settings import Settings

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_asgi_app() -> FastAPI:
    """Build the ASGI application of this process.

    The composition root of the *process*: the only place that reads the environment and
    the only place that configures logging, both of which are global and must happen
    exactly once. Logging is configured here rather than in `create_app` for a second
    reason as well — uvicorn runs its own `dictConfig` in `Config.__init__`, before it
    imports this factory, so ours has to run after the import to win.
    """
    settings = Settings()
    configure_logging(
        level=settings.log_level,
        # The mapping from environment to renderer is a composition decision, not a
        # property of the configuration, so it is spelled out here rather than hidden
        # behind a `Settings.log_renderer`.
        renderer="console" if settings.env == "dev" else "json",
    )
    # The one place the real ports are named. `create_app` takes the factory rather than
    # the ports so that nothing is opened until the application starts serving, and so that
    # a test can hand in a different factory without this module having an opinion.
    return create_app(settings=settings, ports_factory=build_ports)
