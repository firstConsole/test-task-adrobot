from __future__ import annotations

import ast
import inspect
import socket
from typing import TYPE_CHECKING, NoReturn

import pytest

from adrobot.api.routers import health

if TYPE_CHECKING:
    from httpx import AsyncClient


async def test_healthz_answers_ok(client: AsyncClient) -> None:
    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok"}


async def test_readyz_answers_ok_with_nothing_to_check_yet(client: AsyncClient) -> None:
    # The shape 5.8 adds `checks["database"]` to, and the shape 9.6 generates types from.
    # Empty rather than carrying an invented component that cannot fail.
    response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok", "checks": {}}


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
async def test_a_probe_opens_no_socket(
    path: str, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PLAN-00 §5.4 for `/readyz` — an unreachable tracker must not make this service
    # unready — and the liveness rule for `/healthz`: a probe that touches the database
    # turns a slow query into a restart loop.
    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        message = "a health probe must not open a socket"
        raise AssertionError(message)

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    assert (await client.get(path)).status_code == 200


def test_readiness_imports_nothing_from_the_outer_rings() -> None:
    # The same invariant as a static check, so that it survives the day `/readyz` grows a
    # database check that someone is tempted to pair with a tracker ping. import-linter
    # cannot cover this edge: api/ is allowed to see infrastructure/ in general.
    tree = ast.parse(inspect.getsource(health))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert not [
        module
        for module in imported
        if module is not None
        and module.startswith(("adrobot.application", "adrobot.infrastructure"))
    ]


def test_the_health_paths_are_the_literals_the_rest_of_the_repo_copies() -> None:
    # 1.7's HEALTHCHECK, 1.8's compose and 1.12's CI all repeat these two strings
    # (PLAN-00 §5.3). They are exported so that none of them has to retype them.
    assert health.LIVENESS_PATH == "/healthz"
    assert health.READINESS_PATH == "/readyz"
    assert set(health.HEALTH_PATHS) == {"/healthz", "/readyz"}
