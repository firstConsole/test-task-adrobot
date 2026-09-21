"""Part 2 end to end, over the real schema: the API, the scenarios and PostgreSQL.

Everything about these endpoints is asserted against the in-memory database in
`tests/fake_persistence.py`, which is fast, runs on a fresh clone and is a *second*
implementation — worth having only while something holds it to the first. This module is
that something for the editor, as `test_campaigns_on_postgres.py` is for part 1.

What it can prove that the fake cannot:

*   **A removed row really tombstones.** `mirror_state` and `absent_since` move together or
    the table's own CHECK refuses the row — and a dictionary cannot disagree with a CHECK.
*   **BRING BACK really re-elects the row that takes the remainder.** Two ordinals swap, and
    `UNIQUE (draft_id, seq)` with `UNIQUE (draft_id, activated_at)` are checked per row, so a
    rewrite that moved them one at a time collides with itself. `replace_rows` deletes and
    reinserts for exactly this, and only PostgreSQL can say whether that was enough.
*   **The snapshot hash is the shape the column demands.** `base_snapshot_hash ~
    '^[0-9a-f]{64}$'` — a `.digest()` where a `.hexdigest()` belongs would be refused here
    and would otherwise 409 every push for ever.
*   **The audit row holds a JSON array**, which `jsonb_typeof(desired_state) = 'array'` is
    the only thing that can state.
*   **A pushed draft is closed and not deleted**, which the push attempt's own
    `ON DELETE RESTRICT` would turn into an error rather than into silence.

The two behaviours the reference tool is recognised by are here too, held to the real
tables: a removed row that survives the push, and a pin that survives both a push and a
cancel.

Everything below runs inside one transaction the fixtures roll back, so the unit of work
commits exactly as it does in production and the cluster is left as it was found.
"""

from __future__ import annotations

from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from adrobot.api.app import create_app
from adrobot.domain.ids import CampaignId, KeitaroStreamId, OfferId
from adrobot.infrastructure.db.uow import unit_of_work
from tests.fakes import (
    FIRST_CAMPAIGN_ID,
    FIRST_STREAM_ID,
    REFERENCE_OFFERS,
    given_reference_campaign,
)
from tests.helpers import VALID_ENVIRONMENT
from tests.wiring import fake_ports_factory

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

    from adrobot.settings import Settings
    from tests.wiring import FakeWorld

TOKEN = VALID_ENVIRONMENT["ADROBOT_ACCESS_TOKEN"]
OLDEST, NEWEST = REFERENCE_OFFERS
ADDED = OfferId(11111)
ROTATING = KeitaroStreamId(FIRST_STREAM_ID + 1)


@pytest.fixture
def stored(world: FakeWorld, sessions: async_sessionmaker[AsyncSession]) -> FakeWorld:
    """The same fakes, with the in-memory database swapped for the real unit of work."""
    return replace(world, ports=replace(world.ports, unit_of_work=partial(unit_of_work, sessions)))


@pytest.fixture
async def api(settings: Settings, stored: FakeWorld) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings=settings, ports_factory=fake_ports_factory(stored.ports))
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://adrobot.test") as http,
    ):
        http.headers["Authorization"] = f"Bearer {TOKEN}"
        yield http


@pytest.fixture
async def campaign(api: httpx.AsyncClient, world: FakeWorld) -> str:
    """Campaign 93212 from the video, adopted through the import endpoint and the real tables."""
    given_reference_campaign(world.admin)
    answer = await api.post(
        "/api/v1/campaigns/import", json={"keitaro_campaign_id": FIRST_CAMPAIGN_ID}
    )
    assert answer.status_code == 201, answer.text
    return str(answer.json()["id"])


def draft_of(campaign: str) -> str:
    return f"/api/v1/campaigns/{campaign}/streams/{ROTATING}/draft"


def pin_of(campaign: str, offer_id: int) -> str:
    return f"/api/v1/campaigns/{campaign}/streams/{ROTATING}/offers/{offer_id}/pin"


def drawn(flow: dict[str, Any]) -> dict[int, int]:
    return {row["offer_id"]: row["share"] for row in flow["rows"]}


def pinned(flow: dict[str, Any]) -> dict[int, int | None]:
    return {row["offer_id"]: row["pinned_share"] for row in flow["rows"]}


async def stage(
    api: httpx.AsyncClient, campaign: str, *operations: tuple[str, int]
) -> dict[str, Any]:
    answer = await api.post(
        f"{draft_of(campaign)}/operations",
        json={"operations": [{"kind": kind, "offer_id": offer} for kind, offer in operations]},
    )
    assert answer.status_code == 200, answer.text
    return dict(answer.json())


async def rotating(api: httpx.AsyncClient, campaign: str) -> dict[str, Any]:
    answer = await api.get(f"/api/v1/campaigns/{campaign}/streams")
    assert answer.status_code == 200, answer.text
    return next(flow for flow in answer.json()["streams"] if flow["schema"] == "landings")


async def mirrored(
    sessions: async_sessionmaker[AsyncSession], campaign: str
) -> dict[int, tuple[int, bool]]:
    """Read the flow's rows back through the real repositories: share and whether it is out."""
    async with unit_of_work(sessions) as unit, unit.begin() as transaction:
        view = await transaction.streams.lock(
            campaign_id=CampaignId(UUID(campaign)), stream_id=ROTATING
        )
    return {int(row.offer_id): (row.share, row.removed) for row in view.mirror_rows}


# --- the round trip ------------------------------------------------------------------------


async def test_an_edit_and_a_push_carry_the_numbers_through_the_real_schema(
    api: httpx.AsyncClient,
    world: FakeWorld,
    campaign: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    staged = await stage(api, campaign, ("add", ADDED))
    assert drawn(staged) == {ADDED: 34, OLDEST: 33, NEWEST: 33}

    pushed = (await api.post(f"{draft_of(campaign)}/push", json={})).json()

    assert drawn(pushed) == {ADDED: 34, OLDEST: 33, NEWEST: 33}
    assert {int(row.offer_id): row.share for row in world.admin.streams[ROTATING].offers} == {
        OLDEST: 33,
        NEWEST: 33,
        ADDED: 34,
    }
    assert await mirrored(sessions, campaign) == {
        OLDEST: (33, False),
        NEWEST: (33, False),
        ADDED: (34, False),
    }


@pytest.mark.parametrize(
    "merges", [False, True], ids=["tracker-replaces-the-array", "tracker-merges-the-array"]
)
async def test_a_removed_row_survives_the_push_as_a_tombstone_the_check_accepts(
    api: httpx.AsyncClient,
    world: FakeWorld,
    campaign: str,
    db: AsyncConnection,
    merges: bool,  # noqa: FBT001  # a parametrised semantics, not a flag
) -> None:
    """The most characteristic behaviour of the reference tool, over the real tables.

    `ck_stream_offers_absent_since_matches_mirror_state` refuses a half-written tombstone, so
    this passing is the column pair moving together and not merely a flag being set.
    """
    world.admin.merges = merges
    await stage(api, campaign, ("remove", OLDEST))

    pushed = (await api.post(f"{draft_of(campaign)}/push", json={})).json()

    assert drawn(pushed) == {NEWEST: 100, OLDEST: 0}
    assert [row["removed"] for row in pushed["rows"]] == [False, True]
    stored = (
        await db.execute(
            text(
                "SELECT mirror_state, absent_since IS NOT NULL AS dated, state, share "
                "FROM stream_offers WHERE stream_id = :flow AND offer_id = :offer"
            ),
            {"flow": int(ROTATING), "offer": int(OLDEST)},
        )
    ).one()
    # Both readings satisfy `ck_stream_offers_absent_since_matches_mirror_state`, from
    # opposite sides: a tombstone has to be dated, and a row that is merely switched off
    # has to carry no date at all. Either way it takes no traffic and is still drawn.
    if merges:
        assert (stored.mirror_state, stored.dated) == ("present", False)
        assert (stored.state, stored.share) == ("disabled", 0)
    else:
        assert (stored.mirror_state, stored.dated) == ("absent", True)


async def test_a_pin_survives_both_a_cancel_and_a_push(
    api: httpx.AsyncClient, campaign: str, db: AsyncConnection
) -> None:
    """The other behaviour that looks like a bug: a pin outlives everything that clears a draft.

    It can, because it is a row of `offer_pins` and neither closing a draft nor rewriting the
    mirror touches that table. Held here to the real one.
    """
    assert (await api.put(pin_of(campaign, NEWEST), json={"share": 25})).status_code == 200
    await stage(api, campaign, ("add", ADDED))

    cancelled = (await api.delete(draft_of(campaign))).json()
    assert pinned(cancelled)[NEWEST] == 25

    await stage(api, campaign, ("add", ADDED))
    pushed = (await api.post(f"{draft_of(campaign)}/push", json={})).json()

    assert pinned(pushed)[NEWEST] == 25
    held = (
        await db.execute(
            text("SELECT locked_share FROM offer_pins WHERE stream_id = :flow"),
            {"flow": int(ROTATING)},
        )
    ).scalar_one()
    assert held == 25
    assert drawn(pushed) == {ADDED: 38, OLDEST: 37, NEWEST: 25}


async def test_bringing_a_row_back_swaps_ordinals_the_unique_keys_check_one_at_a_time(
    api: httpx.AsyncClient, campaign: str, db: AsyncConnection
) -> None:
    """The video's fourth state, and the write that a naive UPDATE could not perform.

    `UNIQUE (draft_id, activated_at)` is checked per row, so moving one row onto an ordinal
    another still holds collides — which is every BRING BACK. `replace_rows` deletes the
    draft's rows and writes the new set, and this is where that stops being an argument.
    """
    await stage(api, campaign, ("add", ADDED))
    await api.put(pin_of(campaign, NEWEST), json={"share": 25})

    flow = await stage(api, campaign, ("remove", OLDEST), ("bring_back", OLDEST))

    assert drawn(flow) == {OLDEST: 38, ADDED: 37, NEWEST: 25}
    ordinals = (
        await db.execute(
            text(
                "SELECT offer_id, seq, activated_at FROM stream_draft_rows "
                "ORDER BY activated_at DESC"
            )
        )
    ).all()
    assert next(row.offer_id for row in ordinals) == int(OLDEST), (
        "the row that came back is the most recently activated, which is what elects it"
    )
    assert len({row.activated_at for row in ordinals}) == len(ordinals)


# --- the bookkeeping -------------------------------------------------------------------------


async def test_the_snapshot_hash_is_the_shape_the_check_constraint_demands(
    api: httpx.AsyncClient, campaign: str, db: AsyncConnection
) -> None:
    await stage(api, campaign, ("add", ADDED))

    stored = (await db.execute(text("SELECT base_snapshot_hash, status FROM stream_drafts"))).one()

    # `ck_stream_drafts_base_snapshot_hash_is_a_sha256_digest` would have refused the insert,
    # so reaching this line is the assertion. The regexp is repeated because a column that
    # accepted anything would let a `.digest()` through and 409 every push for ever.
    assert len(stored.base_snapshot_hash) == 64
    assert set(stored.base_snapshot_hash) <= set("0123456789abcdef")
    assert stored.status == "open"


async def test_the_push_leaves_an_audit_row_whose_desired_state_is_a_json_array(
    api: httpx.AsyncClient, world: FakeWorld, campaign: str, db: AsyncConnection
) -> None:
    await stage(api, campaign, ("remove", OLDEST))

    await api.post(f"{draft_of(campaign)}/push", json={})

    audit = (
        await db.execute(
            text(
                "SELECT outcome, correlation_id, jsonb_typeof(desired_state) AS kind, "
                "jsonb_array_length(desired_state) AS rows FROM push_attempts"
            )
        )
    ).one()
    assert (audit.outcome, audit.kind, audit.rows) == ("applied", "array", 2)
    assert audit.correlation_id == world.correlation.issued[-1]


async def test_a_pushed_draft_is_closed_and_not_deleted(
    api: httpx.AsyncClient, campaign: str, db: AsyncConnection
) -> None:
    await stage(api, campaign, ("add", ADDED))

    await api.post(f"{draft_of(campaign)}/push", json={})

    # A DELETE would have been refused by `push_attempts.draft_id ON DELETE RESTRICT`, so
    # the row being here in this status is the soft close rather than a lucky absence.
    assert (await db.execute(text("SELECT status FROM stream_drafts"))).scalar_one() == "pushed"
    assert (await rotating(api, campaign))["dirty"] is False


async def test_a_conflict_hands_the_draft_back_over_the_real_tables(
    api: httpx.AsyncClient, world: FakeWorld, campaign: str, db: AsyncConnection
) -> None:
    await stage(api, campaign, ("add", ADDED))
    held, other = world.admin.streams[ROTATING].offers
    world.admin.given_stream(
        replace(world.admin.streams[ROTATING], offers=(replace(held, share=70), other))
    )

    answer = await api.post(f"{draft_of(campaign)}/push", json={})

    assert answer.status_code == 409
    assert answer.json()["code"] == "draft-conflict"
    assert (await db.execute(text("SELECT status FROM stream_drafts"))).scalar_one() == "open"
    assert (await db.execute(text("SELECT outcome FROM push_attempts"))).scalar_one() == "conflict"
    assert (await rotating(api, campaign))["can_push"], "the edits survived, button and all"
