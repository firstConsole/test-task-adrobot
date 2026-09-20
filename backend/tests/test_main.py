from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx
import pytest

import adrobot
import adrobot.main
from adrobot.main import create_asgi_app
from adrobot.settings import UnknownSettingError


def test_importing_main_needs_no_environment() -> None:
    # The reason `main` exposes a factory, checked rather than asserted: with a
    # module-level `app`, importing this module would run the settings validator, and a
    # test, an alembic migration, the CLI and `python -c` would each need a complete
    # environment before they could do anything — with the failure arriving wrapped in
    # the import machinery. A subprocess, because the import has already happened here.
    source_root = Path(adrobot.__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-c", "import adrobot.main"],
        env={"PYTHONPATH": str(source_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_there_is_no_module_level_app() -> None:
    # So that nobody adds one back for convenience. If a future stage wants one, this is
    # the test to change, deliberately, and the 1.7 Dockerfile CMD changes with it.
    assert not hasattr(adrobot.main, "app")


async def test_the_factory_serves_the_liveness_probe(valid_environment: None) -> None:
    # The acceptance criterion of 1.6, through the same entry point uvicorn uses.
    transport = httpx.ASGITransport(app=create_asgi_app())

    async with httpx.AsyncClient(transport=transport, base_url="http://adrobot.test") as http:
        assert (await http.get("/healthz")).status_code == 200


def test_a_typo_in_an_environment_variable_stops_the_process_starting(
    valid_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PLAN-00 §5.1 at the place it is actually promised: start-up, not a unit test of
    # Settings. `docker compose up` with a misspelled variable must not come up healthy
    # and quietly use a default.
    monkeypatch.setenv("ADROBOT_KEITARO_TIMEZOME", "UTC")

    with pytest.raises(UnknownSettingError, match="ADROBOT_KEITARO_TIMEZOME"):
        create_asgi_app()
