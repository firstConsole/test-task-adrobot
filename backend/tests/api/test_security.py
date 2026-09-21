"""That the shared token is the only way in, and that nothing has quietly slipped past it.

The last test is the one that matters. A dependency somebody forgets on a router at stage 7
is an open endpoint that behaves correctly in every other test — it answers, it returns the
right thing, and nobody notices for as long as nobody looks. The check reads the OpenAPI
document, so an endpoint that escapes the guard is also an endpoint the generated frontend
client would call without a token: one failure, not two.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from fastapi import APIRouter, FastAPI

from adrobot.api.app import create_app
from adrobot.api.schemas.problem import PROBLEM_MEDIA_TYPE
from adrobot.api.security import PROTECTED
from tests.helpers import VALID_ENVIRONMENT, unprotected_paths
from tests.wiring import fake_ports_factory

if TYPE_CHECKING:
    from adrobot.settings import Settings

TOKEN = VALID_ENVIRONMENT["ADROBOT_ACCESS_TOKEN"]
GUARDED = "/_guarded"
OPEN = "/_open"


def guarded_router() -> APIRouter:
    """A router built the way every router of this API is built."""
    router = APIRouter(dependencies=PROTECTED)

    @router.get(GUARDED)
    async def guarded() -> dict[str, bool]:
        return {"reached": True}

    return router


def open_router() -> APIRouter:
    """A router somebody forgot to guard, which is the mistake the scan exists to catch."""
    router = APIRouter()

    @router.post(OPEN)
    async def unguarded() -> dict[str, bool]:
        return {"reached": True}

    return router


def application(settings: Settings, *routers: APIRouter) -> FastAPI:
    app = create_app(settings=settings, ports_factory=fake_ports_factory())
    for router in routers:
        app.include_router(router)
    return app


def calling(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://adrobot.test")


async def test_the_shared_token_opens_the_endpoint(settings: Settings) -> None:
    async with calling(application(settings, guarded_router())) as http:
        answer = await http.get(GUARDED, headers={"Authorization": f"Bearer {TOKEN}"})

    assert answer.status_code == 200


async def test_a_request_with_no_token_is_refused_and_told_how(settings: Settings) -> None:
    async with calling(application(settings, guarded_router())) as http:
        answer = await http.get(GUARDED)

    assert answer.status_code == 401
    assert answer.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    # RFC 9110 asks a 401 to say how to authenticate, and `Bearer` rather than `Basic` is
    # what keeps a browser from opening its own credential dialog over the application.
    assert answer.headers["WWW-Authenticate"] == "Bearer"
    assert answer.json()["code"] == "not-authenticated"


async def test_the_wrong_token_is_refused_the_same_way(settings: Settings) -> None:
    async with calling(application(settings, guarded_router())) as http:
        answer = await http.get(GUARDED, headers={"Authorization": "Bearer not-the-token"})

    assert answer.status_code == 401
    assert answer.json()["code"] == "not-authenticated"


async def test_a_credential_of_another_kind_is_not_mistaken_for_one(
    settings: Settings,
) -> None:
    # `Basic` carries a credential and is not this one. FastAPI's own `auto_error` would
    # answer 403 here, which says "you may not" where the truth is "you have not said who
    # you are".
    async with calling(application(settings, guarded_router())) as http:
        answer = await http.get(GUARDED, headers={"Authorization": f"Basic {TOKEN}"})

    assert answer.status_code == 401


async def test_the_refusal_never_repeats_the_token_back(settings: Settings) -> None:
    async with calling(application(settings, guarded_router())) as http:
        answer = await http.get(GUARDED, headers={"Authorization": "Bearer almost-the-token"})

    # Neither the one that was sent nor the one that was expected: a body that echoed either
    # is a body in somebody's browser history and in the proxy's response log.
    assert "almost-the-token" not in answer.text
    assert TOKEN not in answer.text


async def test_the_probes_stay_open(settings: Settings) -> None:
    # A readiness probe cannot hold a credential, and a health check that needs one is a
    # health check that reports the service down when the token is rotated.
    async with calling(application(settings, guarded_router())) as http:
        assert (await http.get("/healthz")).status_code == 200
        assert (await http.get("/readyz")).status_code == 200


def test_the_token_is_not_in_the_published_schema(settings: Settings) -> None:
    schema = application(settings, guarded_router()).openapi()

    assert TOKEN not in str(schema)
    assert schema["components"]["securitySchemes"]["Shared token"]["scheme"] == "bearer"


def test_no_endpoint_escaped_authentication(app: FastAPI) -> None:
    # Vacuous until 6.9 puts the first router on, and deliberately written now: the test
    # below is what proves it will notice.
    assert unprotected_paths(app) == set()


def test_the_scan_notices_an_endpoint_that_escaped(settings: Settings) -> None:
    escaped = application(settings, guarded_router(), open_router())

    assert unprotected_paths(escaped) == {OPEN}
