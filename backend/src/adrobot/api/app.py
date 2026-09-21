"""The FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from importlib import metadata
from typing import TYPE_CHECKING, Final

from fastapi import FastAPI
from starlette.middleware import Middleware
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from adrobot.api.errors import install_error_handlers
from adrobot.api.middleware import AccessLogMiddleware, CorrelationIdMiddleware
from adrobot.api.routers import health

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from contextlib import AbstractAsyncContextManager

    from starlette.types import Lifespan

    from adrobot.composition import AppPorts
    from adrobot.settings import Settings

    PortsFactory = Callable[[Settings], AbstractAsyncContextManager[AppPorts]]

# The largest legitimate body on this API is a batch of draft operations — a few hundred
# offer ids (PLAN-BACKEND §8). A constant and not an `ADROBOT_` variable: it is not
# something an operator tunes per deployment, and every variable is one more name someone
# has to spell correctly under `extra="forbid"`.
MAX_REQUEST_BODY_BYTES: Final = 1024 * 1024


def create_app(*, settings: Settings, ports_factory: PortsFactory) -> FastAPI:
    """Build the HTTP application from an already-validated configuration.

    Pure with respect to the process: it reads no environment variable, opens no socket
    and configures no logging — `main.create_asgi_app` does all three and hands the result
    in. That is what lets an API test build several differently configured applications in
    one process, which is how the production docs policy below is tested at all.

    Keyword-only, and staying that way. PLAN-BACKEND §1 sketches
    `create_app(ports_factory, settings)`; positional parameters in that order would make
    this stage rewrite every call site to insert a first argument, and would have put
    `ports_factory` ahead of `settings` two stages before ports existed.

    `ports_factory` is a factory and not the ports themselves: what it builds is a client, a
    connection pool and a cache, all of which belong to a *running* application and none of
    which may be opened by a function a test calls four times in one process. It is entered
    by the lifespan below, so this factory still opens nothing and still never learns that a
    socket exists.
    """
    # `/docs` is an unauthenticated console pointed at an API whose tracker key can spend
    # money, and the frontend generates its types from a dev or CI run (9.6), never from
    # the deployed service. Derived from `env` rather than from a knob of its own, for the
    # same reason as the constant above — and the same reading decides whether a 5xx says
    # what went wrong or only gives out its correlation id.
    docs_enabled = settings.env != "prod"

    app = FastAPI(
        title="AD Robot API",
        summary="Creates Keitaro campaigns and redistributes offer shares inside a stream.",
        # The installed distribution's version, so that it cannot drift from pyproject.toml
        # inside one artefact.
        version=metadata.version("adrobot"),
        openapi_url="/openapi.json" if docs_enabled else None,
        docs_url="/docs" if docs_enabled else None,
        # One rendering of the schema is enough; ReDoc would be a second surface to turn
        # off in production and a second thing to keep working.
        redoc_url=None,
        # Passed as a list rather than through `add_middleware`, which inserts at position
        # zero — so a sequence of `add_middleware` calls reads in the reverse of the order
        # it builds. Here the first entry is the outermost.
        #
        # There is no CORS middleware, and that is a decision rather than an omission
        # (PLAN-BACKEND §9): dev proxies through Vite, production through nginx, the
        # application is same-origin. A test asserts the absence, because this is the kind
        # of thing a later agent adds to make a browser error go away.
        middleware=[
            Middleware(CorrelationIdMiddleware),
            Middleware(AccessLogMiddleware, quiet_paths=health.HEALTH_PATHS),
            # Innermost of the three, so that a 413 still gets an id and an access line.
            # Note for 6.7: it answers `text/plain`, not `application/problem+json`.
            Middleware(RequestBodyLimitMiddleware, max_body_size=MAX_REQUEST_BODY_BYTES),
        ],
        lifespan=_composed(ports_factory, settings),
    )
    # Every failure of this API is rendered here and in no router: the detail of a 5xx is
    # withheld outside dev, on the same reading of `env` as the docs above.
    install_error_handlers(app, expose_internals=docs_enabled)
    app.include_router(health.router)
    return app


def _composed(ports_factory: PortsFactory, settings: Settings) -> Lifespan[FastAPI]:
    """Hold the process's resources open for as long as the application is serving.

    What the block yields is put on `app.state` rather than returned as the state mapping a
    lifespan may hand back. That mapping is copied into each request scope by the *server*,
    and the suite drives this application through `httpx.ASGITransport`, which is not a
    server; `scope["app"]` is set by Starlette itself, so `app.state` is the one place a
    dependency can read under uvicorn and under a test alike.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with ports_factory(settings) as ports:
            app.state.settings = settings
            app.state.ports = ports
            yield

    return lifespan
