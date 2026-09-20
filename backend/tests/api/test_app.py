from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from adrobot.api.app import create_app
from adrobot.settings import Settings
from tests.helpers import VALID_ENVIRONMENT

if TYPE_CHECKING:
    import pytest
    from fastapi import FastAPI


def test_the_schema_builds_and_documents_both_probes(app: FastAPI) -> None:
    # The guard for the trap the pyproject's flake8-type-checking comment describes: a
    # response model pushed into `if TYPE_CHECKING:` does not fail at import, it fails
    # when FastAPI resolves the handler's annotations — which happens here.
    schema = app.openapi()

    assert set(schema["paths"]) == {"/healthz", "/readyz"}
    assert schema["info"]["title"] == "AD Robot API"
    assert schema["paths"]["/readyz"]["get"]["responses"]["200"]


def test_no_cors_middleware_is_installed(app: FastAPI) -> None:
    # PLAN-BACKEND §9 decides there is none at all: dev proxies through Vite, production
    # through nginx, the application is same-origin. Asserted by class *name*, so that a
    # hand-rolled AllowOriginMiddleware is caught as well as starlette's — this is the
    # kind of thing a later agent adds to make a browser error go away.
    names = [
        str(getattr(entry.cls, "__name__", entry.cls)).casefold() for entry in app.user_middleware
    ]

    assert not [name for name in names if "cors" in name or "origin" in name]


async def test_docs_are_served_outside_production(client: httpx.AsyncClient) -> None:
    assert (await client.get("/docs")).status_code == 200
    assert (await client.get("/openapi.json")).status_code == 200


async def test_docs_are_off_in_production_while_the_probe_still_answers(
    valid_environment: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two applications in one process, which is the whole reason create_app takes settings
    # rather than reading them.
    monkeypatch.setenv("ADROBOT_ENV", "prod")
    transport = httpx.ASGITransport(app=create_app(settings=Settings()))

    async with httpx.AsyncClient(transport=transport, base_url="http://adrobot.test") as http:
        assert (await http.get("/docs")).status_code == 404
        assert (await http.get("/openapi.json")).status_code == 404
        assert (await http.get("/healthz")).status_code == 200


def test_create_app_reads_no_environment(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An API test must not need a complete environment to build an app; and no module
    # below main.py may reach for os.environ behind the composition root's back.
    for name in VALID_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)

    assert create_app(settings=settings).title == "AD Robot API"
