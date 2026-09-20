"""Constants and helpers shared by more than one test module.

Kept out of `conftest.py` on purpose: conftest is for fixtures, and a value or a plain
function is easier to import and to type as one. This file is also the canary for
`from tests.<module> import ...` resolving under `--import-mode=importlib` at all, which
is the shape `tests/fakes.py` needs at stage 4.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable, Table

from adrobot.domain.ids import OfferId
from adrobot.domain.shares import OfferRow
from adrobot.infrastructure.db import models  # noqa: F401  # registers the tables on Base
from adrobot.infrastructure.db.base import Base

if TYPE_CHECKING:
    from collections.abc import Iterable
    from io import StringIO

# Written out here rather than imported from `adrobot.settings`. The prefix is a
# cross-component decision (PLAN-00 §5.1) that compose, the Makefile, CI and .env.example
# all spell literally; a test that imported it could not notice it changing.
ENV_PREFIX: Final = "ADROBOT_"

# One canonical working environment, so that a test which cares about a single variable
# states only that variable. Kept in step with the repository's .env.example by
# tests/test_settings.py::test_env_example_declares_exactly_the_model_fields, which parses
# that file and compares it with the model in both directions.
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


def offer_row(  # noqa: PLR0913  # one keyword per field of OfferRow, by design
    offer_id: int,
    *,
    seq: int,
    activated_at: int | None = None,
    share: int = 0,
    pinned_share: int | None = None,
    removed: bool = False,
) -> OfferRow:
    """Build one kernel row.

    `activated_at` defaults to `seq`, which is the state a fetch from the tracker produces:
    a row's creation order is also the order it was last activated in. The tests that care
    about the tie-break — every one taken from the video — pass it explicitly.
    """
    return OfferRow(
        offer_id=OfferId(offer_id),
        seq=seq,
        activated_at=seq if activated_at is None else activated_at,
        share=share,
        pinned_share=pinned_share,
        removed=removed,
    )


def shares(rows: Iterable[OfferRow]) -> dict[int, int]:
    """Offer id to share — the shape every assertion about the arithmetic is made in.

    A dict and never a tuple: the wrong tie-break rule produces the same multiset of values
    (34, 33, 33) against the right one, and only the pairing tells them apart. That pairing
    is also what a reviewer sees when they open the stream in Keitaro.
    """
    return {int(row.offer_id): row.share for row in rows}


# SQLAlchemy does not annotate its dialect constructors, and this is the suite's one
# PostgreSQL dialect.
POSTGRES = postgresql.dialect()  # type: ignore[no-untyped-call]


def table(table_name: str) -> Table:
    """One mapped table. Through the metadata and not through `cls.__table__`, which
    DeclarativeBase types as a `FromClause` with no indexes or constraints on it."""
    return Base.metadata.tables[table_name]


def ddl(table_name: str) -> str:
    """Compile one table's CREATE TABLE, which is where a server default and an enumeration's
    CHECK render as the literals a migration will carry."""
    return str(CreateTable(table(table_name)).compile(dialect=POSTGRES))


def checks(table_name: str) -> dict[str, str]:
    """Every CHECK constraint on one table, by name."""
    return {
        str(constraint.name): str(constraint.sqltext)
        for constraint in table(table_name).constraints
        if isinstance(constraint, CheckConstraint)
    }
