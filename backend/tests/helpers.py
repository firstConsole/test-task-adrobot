"""Constants and helpers shared by more than one test module.

Kept out of `conftest.py` on purpose: conftest is for fixtures, and a value or a plain
function is easier to import and to type as one. This file is also the canary for
`from tests.<module> import ...` resolving under `--import-mode=importlib` at all, which
is the shape `tests/fakes.py` needs at stage 4.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from io import StringIO

# Written out here rather than imported from `adrobot.settings`. The prefix is a
# cross-component decision (PLAN-00 §5.1) that compose, the Makefile, CI and .env.example
# all spell literally; a test that imported it could not notice it changing.
ENV_PREFIX: Final = "ADROBOT_"

# One canonical working environment, so that a test which cares about a single variable
# states only that variable. Kept in step with .env.example (1.8) by the field-set test in
# tests/test_settings.py, which fails in both directions.
VALID_ENVIRONMENT: Final[dict[str, str]] = {
    "ADROBOT_ENV": "dev",
    "ADROBOT_DATABASE_URL": "postgresql+asyncpg://adrobot:adrobot@db:5432/adrobot",
    "ADROBOT_ACCESS_TOKEN": "access-token-for-tests-not-a-real-one",
    "ADROBOT_KEITARO_BASE_URL": "https://tracker.invalid/admin_api/v1",
    # Distinctive enough that a leak test grepping for it cannot match anything else, and
    # obviously not a credential, so gitleaks (1.11) stays quiet.
    "ADROBOT_KEITARO_API_KEY": "kt-fake-key-4a7d21c9e05b",
    "ADROBOT_KEITARO_PUBLIC_BASE_URL": "https://tracker.invalid",
}


def log_records(stream: StringIO) -> list[dict[str, Any]]:
    """Parse the JSON lines a `log_stream`-configured logger has written so far."""
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def records_named(stream: StringIO, event: str) -> list[dict[str, Any]]:
    """Return only the records whose `event` field equals `event`."""
    return [record for record in log_records(stream) if record.get("event") == event]
