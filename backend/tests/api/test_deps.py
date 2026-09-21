"""That a request reaches the ports, and that it reaches the *same* ports every time.

There are no routers using them yet — those arrive at 6.9 — so this drives a probe router of
its own. That is deliberate: the wiring is what this stage delivers, and a stage whose only
test is "the next stage will notice" delivers nothing.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from adrobot.api.app import create_app
from adrobot.api.deps import PortsDep, SettingsDep, UowDep
from tests.wiring import fake_ports_factory

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from adrobot.application.ports.persistence import UnitOfWork
    from adrobot.composition import AppPorts
    from adrobot.settings import Settings
    from tests.wiring import FakeWorld

PROBE = "/_probe"


def probe_router() -> APIRouter:
    """A router that answers with what its dependencies handed it."""
    router = APIRouter()

    @router.get(PROBE)
    async def probe(ports: PortsDep, settings: SettingsDep, uow: UowDep) -> dict[str, Any]:
        async with uow.begin() as transaction:
            page = await transaction.campaigns.page(after=None, query=None, limit=1)
        return {
            "alias": ports.aliases.new(),
            "environment": settings.env,
            "campaigns": len(page.campaigns),
        }

    return router


async def probing(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://adrobot.test")


async def test_a_handler_is_handed_the_ports_the_settings_and_a_unit_of_work(
    app: FastAPI, world: FakeWorld
) -> None:
    app.include_router(probe_router())

    async with app.router.lifespan_context(app), await probing(app) as http:
        answered = (await http.get(PROBE)).json()

    assert answered["alias"] == world.aliases.issued[0]
    assert answered["environment"] == "dev"
    assert answered["campaigns"] == 0


async def test_the_ports_are_composed_once_for_the_application_and_not_once_per_request(
    settings: Settings, world: FakeWorld
) -> None:
    entered = 0

    @asynccontextmanager
    async def counting(_: Settings) -> AsyncIterator[AppPorts]:
        nonlocal entered
        entered += 1
        yield world.ports

    app = create_app(settings=settings, ports_factory=counting)
    app.include_router(probe_router())

    async with app.router.lifespan_context(app), await probing(app) as http:
        await http.get(PROBE)
        await http.get(PROBE)

    # One tracker client and one connection pool for the process. A factory entered per
    # request would open both on every call and throw them away afterwards.
    assert entered == 1


async def test_each_request_gets_its_own_unit_of_work_and_gives_it_back(
    settings: Settings, world: FakeWorld
) -> None:
    opened: list[str] = []

    @asynccontextmanager
    async def watched() -> AsyncIterator[UnitOfWork]:
        opened.append("in")
        try:
            yield world.uow
        finally:
            opened.append("out")

    app = create_app(
        settings=settings,
        ports_factory=fake_ports_factory(replace(world.ports, unit_of_work=watched)),
    )
    app.include_router(probe_router())

    async with app.router.lifespan_context(app), await probing(app) as http:
        await http.get(PROBE)
        await http.get(PROBE)

    # Opened when the request arrives and closed when it is answered, twice over: a session
    # left open is a pool connection the next request cannot have.
    assert opened == ["in", "out", "in", "out"]


async def test_an_application_whose_lifespan_never_ran_says_so(app: FastAPI) -> None:
    app.include_router(probe_router())

    async with await probing(app) as http:
        with pytest.raises(RuntimeError, match="lifespan"):
            await http.get(PROBE)
