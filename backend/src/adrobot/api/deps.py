"""The dependencies a route declares, as `Annotated` aliases and nothing else.

A route asks for `UowDep` and is handed one request's unit of work, opened before the
handler runs and closed after it returns — including when it raises, which is what makes
"a use case never has to close anything" true rather than aspirational.

The aliases are the module's whole public surface, and the ruff configuration exempts this
file from the type-checking rules on that basis: an `Annotated[...]` alias is resolved at
runtime by FastAPI, so an import moved into `if TYPE_CHECKING:` here would not fail — it
would silently turn a dependency into a query parameter and answer 422 with nothing wired.

The providers below are private because they are the aliases' implementation. Nothing else
may call them: a handler that called `_ports(request)` itself would be reaching around the
dependency that is supposed to own the lifetime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, cast

from fastapi import Depends, Request

from adrobot.application.ports.persistence import UnitOfWork
from adrobot.composition import AppPorts
from adrobot.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _held(request: Request, name: str) -> object:
    """Read one thing the lifespan put on the application, or say that it never ran.

    `app.state` and not the mapping a lifespan may yield: that mapping is copied into each
    request scope by the *server*, and the suite drives the application through
    `httpx.ASGITransport`, which is not one. `scope["app"]` is set by Starlette itself, so
    this reads the same object under uvicorn and under a test.
    """
    held = getattr(request.app.state, name, None)
    if held is None:
        message = (
            f"the application has no {name}: it was built without running its lifespan, "
            f"which is what composes the ports"
        )
        raise RuntimeError(message)
    return held


def _ports(request: Request) -> AppPorts:
    return cast("AppPorts", _held(request, "ports"))


def _settings(request: Request) -> Settings:
    return cast("Settings", _held(request, "settings"))


async def _unit_of_work(ports: Annotated[AppPorts, Depends(_ports)]) -> AsyncIterator[UnitOfWork]:
    """Open one unit of work for this request and close it when the response is finished.

    A generator dependency rather than a use case opening its own: the session is a resource
    of the request, and FastAPI is the only thing that knows when a request is over.
    """
    async with ports.unit_of_work() as unit:
        yield unit


PortsDep = Annotated[AppPorts, Depends(_ports)]
SettingsDep = Annotated[Settings, Depends(_settings)]
# `UnitOfWork` is imported above and not under `TYPE_CHECKING`, which is the whole reason
# this module is exempt from the type-checking rules: FastAPI resolves a handler's
# annotations against the *handler's* module, where a name this file kept to itself does not
# exist — and the failure is a NameError at import time if the alias is a forward reference,
# or a silent demotion to a query parameter if it is not.
UowDep = Annotated[UnitOfWork, Depends(_unit_of_work)]
