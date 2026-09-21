"""Part 1 end to end, over the real schema: the API, the scenarios and PostgreSQL.

Everything else about these endpoints is asserted against the in-memory database in
`tests/fake_persistence.py`, which is fast, runs on a fresh clone and is a *second*
implementation — and a second implementation is only worth having while something holds it
to the first. This module is that something.

What it can prove that the fake cannot:

*   the columns exist and take what the mappers put in them, `requested_country` as a
    `CountryCode` and a flow's filters as JSON included;
*   `upsert_campaign_streams` really tombstones — `mirror_state` and `absent_since` move
    together or the table's own CHECK refuses the row;
*   the keyset page really is a keyset page, through the index and the cursor a client hands
    back;
*   `note_fetched` writes the four columns the tracker owns and leaves `setup_status` alone,
    which is a CASE expression in SQL rather than an `if` in Python.

Everything below runs inside one transaction the fixtures roll back, so the unit of work
commits exactly as it does in production and the cluster is left as it was found.
"""

from __future__ import annotations

from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from sqlalchemy import text

from adrobot.api.app import create_app
from adrobot.domain.campaign import FIRST_FLOW, SECOND_FLOW
from adrobot.domain.ids import CampaignId, KeitaroStreamId
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
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

    from adrobot.settings import Settings
    from tests.wiring import FakeWorld

CAMPAIGNS = "/api/v1/campaigns"
TOKEN = VALID_ENVIRONMENT["ADROBOT_ACCESS_TOKEN"]
MADE_UP = {"name": "Summer MX", "country": "MX", "offer_id": int(REFERENCE_OFFERS[0])}


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


async def create(api: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    answer = await api.post(CAMPAIGNS, json=MADE_UP | overrides)
    assert answer.status_code == 201, answer.text
    return dict(answer.json())


async def mirrored(sessions: async_sessionmaker[AsyncSession], campaign: UUID) -> dict[str, Any]:
    """Read the campaign back the way the editor will, through the real repositories."""
    async with unit_of_work(sessions) as unit, unit.begin() as transaction:
        views = await transaction.streams.views_for(CampaignId(campaign))
        return {view.stream.name: view for view in views}


async def test_a_created_campaign_is_really_in_the_database_with_both_flows(
    api: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    body = await create(api)

    flows = await mirrored(sessions, body["id"])
    assert sorted(flows) == [FIRST_FLOW, SECOND_FLOW]
    assert flows[FIRST_FLOW].stream.filters[0].payload == ("MX",)
    # The one number this project is judged on, carried through the mapper, the JSON column
    # and the tie-break ordering rather than through a dictionary.
    assert {row.offer_id: row.share for row in flows[SECOND_FLOW].mirror_rows} == {
        REFERENCE_OFFERS[0]: 100
    }


async def test_the_row_keeps_what_part_one_was_asked_for(
    api: httpx.AsyncClient, db: AsyncConnection
) -> None:
    body = await create(api)

    stored = (
        await db.execute(
            text(
                "SELECT setup_status, requested_country, requested_offer_id, public_domain, "
                "synced_at IS NOT NULL AS looked FROM campaigns WHERE id = :id"
            ),
            {"id": body["id"]},
        )
    ).one()

    # A raw SELECT, so the enum arrives as the text PostgreSQL holds — which is the half
    # of the mapping an ORM read would answer from its own converter rather than from the
    # column.
    assert stored.setup_status == "ready"
    assert (stored.requested_country, stored.requested_offer_id) == ("MX", REFERENCE_OFFERS[0])
    assert stored.public_domain == "track.example"
    assert stored.looked


async def test_a_flow_deleted_in_the_tracker_becomes_a_tombstone_the_check_accepts(
    api: httpx.AsyncClient, world: FakeWorld, sessions: async_sessionmaker[AsyncSession]
) -> None:
    body = await create(api)
    geo_flow = next(flow.id for flow in world.admin.streams.values() if flow.name == FIRST_FLOW)
    del world.admin.streams[geo_flow]

    answer = await api.post(f"{CAMPAIGNS}/{body['id']}/refetch")

    assert answer.status_code == 200
    flows = await mirrored(sessions, body["id"])
    # `mirror_state` and `absent_since` move together or `ck_streams_tombstone_is_dated`
    # refuses the row — which is the half of tombstoning an in-memory dictionary cannot
    # disagree with.
    assert flows[FIRST_FLOW].stream.absent
    assert not flows[SECOND_FLOW].stream.absent


async def test_a_refetch_leaves_the_column_a_fetch_may_not_touch(
    api: httpx.AsyncClient, world: FakeWorld, db: AsyncConnection
) -> None:
    world.admin.fail_on("create_stream", RuntimeError("never mind"))
    with pytest.raises(RuntimeError):
        await api.post(CAMPAIGNS, json=MADE_UP)
    world.admin.failures.clear()
    half_built = (await api.get(CAMPAIGNS)).json()["campaigns"][0]

    await api.post(f"{CAMPAIGNS}/{half_built['id']}/refetch")

    still = (
        await db.execute(
            text("SELECT setup_status FROM campaigns WHERE id = :id"), {"id": half_built["id"]}
        )
    ).scalar_one()
    # A fetch that reset this would undo the one signal that a campaign needs finishing, and
    # the CASE in `note_fetched` is what keeps it out of the four columns it writes.
    assert still == "needs_attention"


async def test_the_keyset_page_walks_the_whole_list_and_repeats_nothing(
    api: httpx.AsyncClient,
) -> None:
    # Every row here shares one `created_at`, and not by contrivance: the fixtures hold the
    # whole test inside one transaction they roll back, so PostgreSQL's `now()` — which is
    # the transaction's clock, not the statement's — is identical for all three. That is the
    # case the cursor carries an id for, and a cursor on the timestamp alone either repeats a
    # row here or loses one.
    for name in ("first", "second", "third"):
        await create(api, name=name)

    page = await api.get(CAMPAIGNS, params={"limit": 2})
    rest = await api.get(CAMPAIGNS, params={"limit": 2, "after": page.json()["next_cursor"]})

    walked = [campaign["name"] for campaign in page.json()["campaigns"]] + [
        campaign["name"] for campaign in rest.json()["campaigns"]
    ]
    assert sorted(walked) == ["first", "second", "third"]
    assert rest.json()["next_cursor"] is None


async def test_an_imported_campaign_keeps_the_shares_the_tracker_holds(
    api: httpx.AsyncClient, world: FakeWorld, sessions: async_sessionmaker[AsyncSession]
) -> None:
    given_reference_campaign(world.admin)

    body = (
        await api.post(f"{CAMPAIGNS}/import", json={"keitaro_campaign_id": FIRST_CAMPAIGN_ID})
    ).json()

    flows = await mirrored(sessions, body["id"])
    assert flows["Flow 2"].stream.keitaro_stream_id == KeitaroStreamId(FIRST_STREAM_ID + 1)
    # 25 and 25 through the real mapper as well: nothing on the way to the database
    # normalises a flow somebody else built.
    assert {row.offer_id: row.share for row in flows["Flow 2"].mirror_rows} == {
        REFERENCE_OFFERS[0]: 25,
        REFERENCE_OFFERS[1]: 25,
    }
