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
from sqlalchemy.schema import CreateIndex, CreateTable, Table

from adrobot.api.routers.health import HEALTH_PATHS
from adrobot.application.ports.persistence import CampaignSetup
from adrobot.domain.campaign import CampaignSetupStatus
from adrobot.domain.ids import OfferId
from adrobot.domain.shares import OfferRow
from adrobot.infrastructure.db import models  # noqa: F401  # registers the tables on Base
from adrobot.infrastructure.db.base import Base
from tests.fakes import FIRST_MOMENT, given_reference_campaign

if TYPE_CHECKING:
    from collections.abc import Iterable
    from io import StringIO

    from fastapi import FastAPI

    from adrobot.application.ports.persistence import StreamView, UnitOfWork
    from adrobot.domain.ids import CampaignId, KeitaroStreamId
    from tests.wiring import FakeWorld

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


def index_ddl(table_name: str, index_name: str) -> str:
    """Compile one index's CREATE INDEX. The access method and the operator class live only
    here — alembic compares neither, so nothing else can notice them going missing."""
    index = next(ix for ix in table(table_name).indexes if ix.name == index_name)
    return str(CreateIndex(index).compile(dialect=POSTGRES))


# --- the database tier -------------------------------------------------------------------

# Read from the plain environment and not from `ADROBOT_*`: the autouse `_isolated_environment`
# deletes every prefixed variable before a test runs, and `Settings` refuses a prefixed name it
# does not declare — so an `ADROBOT_TEST_DATABASE_URL` would be erased by one guard and
# rejected by the other.
DATABASE_URL_VARIABLE: Final = "TEST_DATABASE_URL"

# What compose publishes on the host. `VALID_ENVIRONMENT` points at `db`, the service name,
# which resolves only inside the compose network.
LOCAL_DATABASE_URL: Final = "postgresql+asyncpg://adrobot:adrobot@127.0.0.1:5432/adrobot"

# Deliberately not `alembic_version`: the schema is migrated before the suite runs, and a
# sweep that emptied it would leave the cluster looking unmigrated to the next run.
TRUNCATE_EVERY_TABLE: Final = "TRUNCATE {} CASCADE".format(
    ", ".join(mapped.name for mapped in Base.metadata.sorted_tables)
)

# One statement rather than one per table, so the census a run ends with is one round trip.
ROWS_PER_TABLE: Final = " UNION ALL ".join(
    # Safe here and nowhere else: the only interpolation is a table name read off our own
    # MetaData, and a relation cannot be a bind parameter.
    f"SELECT '{mapped.name}' AS relation, count(*) AS rows FROM {mapped.name}"  # noqa: S608
    for mapped in Base.metadata.sorted_tables
)

TRANSACTION_CONTROL: Final = frozenset({"SAVEPOINT", "RELEASE", "ROLLBACK"})


class Statements:
    """Every statement one engine sent while the `statements` fixture was installed, in order.

    A test asserts on the verbs and on how many there are, never on the text: the count is the
    design decision — four statements for the editor read whatever the flow count — while the
    text is SQLAlchemy's rendering and changes under a patch release.

    `BEGIN` and `COMMIT` never appear: asyncpg issues them on the connection rather than
    through a cursor. `SAVEPOINT` and `RELEASE` do, because the `db` fixture holds a
    transaction open around the test — measured, the same `views_for` is
    `('SAVEPOINT', 'SELECT', 'SELECT', 'RELEASE')` on the rolled-back connection and
    `('SELECT', 'SELECT')` off the pool, which is why a count asserts on `queries`.
    """

    def __init__(self) -> None:
        self.sent: list[str] = []

    @property
    def verbs(self) -> tuple[str, ...]:
        """Every statement as the word that begins it, the fixture's own savepoints included."""
        return tuple(statement.split(maxsplit=1)[0].upper() for statement in self.sent)

    @property
    def queries(self) -> tuple[str, ...]:
        """The verbs the code under test chose, without the transaction control around them."""
        return tuple(verb for verb in self.verbs if verb not in TRANSACTION_CONTROL)

    def __len__(self) -> int:
        return len(self.queries)

    def __repr__(self) -> str:
        return f"Statements{self.verbs!r}"


def unprotected_paths(app: FastAPI) -> set[str]:
    """Return the paths of every operation the OpenAPI document says needs no credential.

    `app.openapi()["paths"]` and never `app.routes`: `include_router` appends one lazy
    router object rather than flattening it, so filtering `app.routes` for `APIRoute`
    answers with an empty set — and a check that inspects nothing passes.

    The document is the right thing to read for a second reason: it is what the frontend
    generates its client from, so an endpoint that is open here is an endpoint the generated
    client will call without a token, and the two failures are the same failure.
    """
    return {
        path
        for path, operations in app.openapi()["paths"].items()
        if path not in HEALTH_PATHS
        for operation in operations.values()
        if not operation.get("security")
    }


# --- the editor's own fixture ------------------------------------------------------------

ROTATING_FLOW: Final = 1
"""Where Flow 2 sits in the list the tracker answers with. It is the one with
`schema: landings`, so it is the only one of the reference campaign's two that rotates
offers — and therefore the only one part 2 is about."""


async def given_mirrored_campaign(world: FakeWorld) -> tuple[CampaignId, KeitaroStreamId]:
    """Put campaign 93212 and its two flows into the tracker and into the mirror.

    The fixture every editor test starts from, and deliberately the campaign from the video:
    a failing assertion then reads like something a reviewer can open in Keitaro. Flow 2's
    two offers hold 25% each, which is a clean state summing to 50 — every scenario that
    touches this campaign has to survive that.
    """
    campaign = given_reference_campaign(world.admin)
    streams = await world.admin.list_campaign_streams(campaign.id)
    async with world.uow.begin() as transaction:
        row = await transaction.campaigns.add(
            campaign, setup=CampaignSetup(status=CampaignSetupStatus.READY)
        )
        await transaction.streams.upsert_campaign_streams(
            campaign_id=row.id, streams=streams, at=FIRST_MOMENT
        )
    return row.id, streams[ROTATING_FLOW].id


async def locked_view(
    uow: UnitOfWork, campaign_id: CampaignId, stream_id: KeitaroStreamId
) -> StreamView:
    """Read one flow whole — mirror, pins and live draft — the way every write starts."""
    async with uow.begin() as transaction:
        return await transaction.streams.lock(campaign_id=campaign_id, stream_id=stream_id)
