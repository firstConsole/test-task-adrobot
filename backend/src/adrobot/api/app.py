"""The FastAPI application factory."""

from __future__ import annotations

from importlib import metadata
from typing import TYPE_CHECKING, Final

from fastapi import FastAPI
from starlette.middleware import Middleware
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from adrobot.api.middleware import AccessLogMiddleware, CorrelationIdMiddleware
from adrobot.api.routers import health

if TYPE_CHECKING:
    from adrobot.settings import Settings

# The largest legitimate body on this API is a batch of draft operations — a few hundred
# offer ids (PLAN-BACKEND §8). A constant and not an `ADROBOT_` variable: it is not
# something an operator tunes per deployment, and every variable is one more name someone
# has to spell correctly under `extra="forbid"`.
MAX_REQUEST_BODY_BYTES: Final = 1024 * 1024


def create_app(*, settings: Settings) -> FastAPI:
    """Build the HTTP application from an already-validated configuration.

    Pure with respect to the process: it reads no environment variable, opens no socket
    and configures no logging — `main.create_asgi_app` does all three and hands the result
    in. That is what lets an API test build several differently configured applications in
    one process, which is how the production docs policy below is tested at all.

    Keyword-only, and staying that way. PLAN-BACKEND §1 sketches
    `create_app(ports_factory, settings)`; positional parameters in that order would make
    6.6 rewrite every call site to insert a first argument, and would put `ports_factory`
    ahead of `settings` two stages before ports exist. 6.6 adds one keyword-only parameter
    beside this one and no call site changes.
    """
    # `/docs` is an unauthenticated console pointed at an API whose tracker key can spend
    # money, and the frontend generates its types from a dev or CI run (9.6), never from
    # the deployed service. Derived from `env` rather than from a knob of its own, for the
    # same reason as the constant above.
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
        # Still no `lifespan`, and from 4.3 that is a decision rather than an absence of
        # anything to hold open. The tracker client has an owner of its own —
        # `keitaro_transport(settings)`, in infrastructure/keitaro/transport.py — so the
        # resource's lifetime lives in the module that knows what the resource is. 6.6
        # wraps that in `async with ports_factory(settings) as ports` and hands the ports
        # in; this factory still never learns that a socket exists.
    )
    app.include_router(health.router)
    return app
