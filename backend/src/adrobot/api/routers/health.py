"""Liveness and readiness probes.

The two paths are a contract, not decoration: the same literals go into the Dockerfile
`HEALTHCHECK` (1.7), into `docker-compose.yml` (1.8) and into CI (1.12), so they are
written here once and imported (PLAN-00 §5.3).

`/readyz` never talks to Keitaro (PLAN-00 §5.4). An unreachable tracker does not make this
service unready — the editor, the draft and the mirror all work without it — so tracker
availability is a field in a response body and a banner in the UI, never a readiness
verdict. A test parses this module and fails if it grows an import from `application/` or
`infrastructure/`, the rings a tracker call could come from.
"""

from __future__ import annotations

from typing import Final, Literal

from fastapi import APIRouter
from pydantic import BaseModel

LIVENESS_PATH: Final = "/healthz"
READINESS_PATH: Final = "/readyz"
HEALTH_PATHS: Final = frozenset({LIVENESS_PATH, READINESS_PATH})

router = APIRouter(tags=["health"])

ComponentStatus = Literal["ok", "error"]


class LivenessResponse(BaseModel):
    """Body of `/healthz`."""

    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    """Body of `/readyz`.

    `checks` maps a component name to its verdict and is empty at 1.6, which is the
    honest answer: nothing this process depends on exists yet. 5.8 adds
    `checks["database"]` and, with it, the 503 branch and the `503` entry in this
    operation's OpenAPI responses — all three in one commit, and all before the frontend
    generates its types from this schema at 9.6. The shape around `checks` does not move.
    """

    status: ComponentStatus
    checks: dict[str, ComponentStatus]


@router.get(LIVENESS_PATH, summary="Liveness probe")
async def healthz() -> LivenessResponse:
    """Answer 200 for as long as the process can serve a request.

    Dependency-free on purpose: a liveness probe that touches the database turns a slow
    query into a container restart loop.
    """
    return LivenessResponse(status="ok")


@router.get(READINESS_PATH, summary="Readiness probe")
async def readyz() -> ReadinessResponse:
    """Report whether this process is ready to take traffic.

    At 1.6 it has nothing to ask: there is no database yet, and the tracker is not
    allowed to be part of this verdict. It answers 200 with an empty `checks` rather than
    inventing a component that cannot fail.
    """
    return ReadinessResponse(status="ok", checks={})
