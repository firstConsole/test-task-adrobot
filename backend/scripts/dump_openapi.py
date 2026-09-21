"""Write this service's OpenAPI document — the file the frontend generates its types from.

    cd backend && poetry run python scripts/dump_openapi.py [path]

The environment is taken from `.env.example`, which `tests/test_settings.py` holds to
exactly the fields of `Settings`, and never from the caller's: the document has to come out
the same on every machine, and regenerating the frontend's types must not need a real key.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from adrobot.api.app import create_app
from adrobot.composition import build_ports
from adrobot.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "adrobot-openapi.json"


def _example_environment() -> dict[str, str]:
    """Parse the `NAME=value` lines of `.env.example`."""
    values: dict[str, str] = {}
    for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        name, separator, value = line.strip().partition("=")
        if separator and not name.startswith("#"):
            values[name.strip()] = value.strip()
    return values


def main(argv: Sequence[str]) -> int:
    """Render the document to `argv[0]`, or to `docs/adrobot-openapi.json`."""
    os.environ.clear()
    os.environ.update(_example_environment())

    document = create_app(settings=Settings(), ports_factory=build_ports).openapi()
    # Sorted, so that the diff this file exists for reports a schema change and not a
    # route that moved within its module.
    rendered = json.dumps(document, indent=2, sort_keys=True)
    Path(argv[0] if argv else DEFAULT_OUTPUT).write_text(f"{rendered}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
