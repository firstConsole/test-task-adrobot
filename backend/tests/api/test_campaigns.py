"""The campaign endpoints, driven the way curl and the frontend drive them.

These are the requests a reviewer makes by hand, in the order they make them: create a
campaign, look at the list, open one somebody else built. What is asserted is the body they
will read and the state the tracker is left in.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from adrobot.domain.campaign import FIRST_FLOW, SECOND_FLOW
from adrobot.domain.ids import KeitaroCampaignId
from tests.fakes import FIRST_CAMPAIGN_ID, given_reference_campaign
from tests.helpers import VALID_ENVIRONMENT

if TYPE_CHECKING:
    import httpx

    from tests.wiring import FakeWorld

CAMPAIGNS = "/api/v1/campaigns"
TOKEN = VALID_ENVIRONMENT["ADROBOT_ACCESS_TOKEN"]
MADE_UP = {"name": "Summer MX", "country": "MX", "offer_id": 3749}


@pytest.fixture
def api(client: httpx.AsyncClient) -> httpx.AsyncClient:
    """The application's client, carrying the shared token the way every caller must."""
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    return client


async def create(api: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    answer = await api.post(CAMPAIGNS, json=MADE_UP | overrides)
    assert answer.status_code == 201, answer.text
    return dict(answer.json())


async def test_creating_a_campaign_answers_with_it_and_leaves_two_flows_in_the_tracker(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    body = await create(api)

    assert body["keitaro_campaign_id"] == FIRST_CAMPAIGN_ID
    assert body["name"] == "Summer MX"
    assert body["setup_status"] == "ready"
    assert body["setup_failure"] is None
    assert body["public_url"] == f"https://track.example/{body['alias']}"
    assert body["requested_country"] == "MX"
    assert sorted(flow.name for flow in world.admin.streams.values()) == [FIRST_FLOW, SECOND_FLOW]


async def test_a_geo_that_is_not_a_country_is_refused_under_its_own_field(
    api: httpx.AsyncClient,
) -> None:
    answer = await api.post(CAMPAIGNS, json=MADE_UP | {"country": "XX"})

    assert answer.status_code == 422
    body = answer.json()
    # The field, not just the request: this is what a form highlights. `XX` passes the
    # regexp everybody writes first, and a campaign aimed at it never sees a click.
    assert [field["location"] for field in body["errors"]] == ["body.country"]
    assert "country code" in body["errors"][0]["message"]


async def test_a_field_this_endpoint_does_not_take_is_refused(api: httpx.AsyncClient) -> None:
    answer = await api.post(CAMPAIGNS, json=MADE_UP | {"share": 100})

    # `extra="forbid"`: a client sending shares is a client that has reimplemented the
    # arithmetic, and the API deliberately has nowhere to put them.
    assert answer.status_code == 422
    assert [field["location"] for field in answer.json()["errors"]] == ["body.share"]


async def test_the_campaign_endpoints_need_the_token(client: httpx.AsyncClient) -> None:
    answer = await client.post(CAMPAIGNS, json=MADE_UP)

    assert answer.status_code == 401
    assert answer.json()["code"] == "not-authenticated"


async def test_importing_opens_a_campaign_somebody_else_built(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    given_reference_campaign(world.admin)

    answer = await api.post(f"{CAMPAIGNS}/import", json={"keitaro_campaign_id": FIRST_CAMPAIGN_ID})

    assert answer.status_code == 201
    body = answer.json()
    assert body["name"] == "AU | Oxys"
    assert body["setup_status"] == "ready"
    # Not ours to finish, and no link to build: the tracker does not say which domain a
    # campaign is served on.
    assert body["requested_country"] is None
    assert body["public_url"] is None


async def test_importing_the_same_campaign_twice_answers_409_naming_the_first(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    given_reference_campaign(world.admin)
    body = {"keitaro_campaign_id": FIRST_CAMPAIGN_ID}
    first = await api.post(f"{CAMPAIGNS}/import", json=body)

    answer = await api.post(f"{CAMPAIGNS}/import", json=body)

    assert answer.status_code == 409
    assert answer.json()["code"] == "campaign-already-imported"
    # The screen can offer to open it instead of only refusing the request.
    assert answer.json()["campaign_id"] == first.json()["id"]


async def test_a_campaign_the_tracker_does_not_have_answers_404(api: httpx.AsyncClient) -> None:
    answer = await api.post(f"{CAMPAIGNS}/import", json={"keitaro_campaign_id": 404})

    assert answer.status_code == 404
    assert answer.json()["code"] == "tracker-has-no-such-thing"


async def test_the_list_is_newest_first(api: httpx.AsyncClient, world: FakeWorld) -> None:
    for name in ("first", "second", "third"):
        await create(api, name=name)
        world.clock.advance(timedelta(minutes=1))

    listed = (await api.get(CAMPAIGNS)).json()

    assert [campaign["name"] for campaign in listed["campaigns"]] == ["third", "second", "first"]
    assert listed["next_cursor"] is None


async def test_a_page_continues_exactly_where_the_last_one_ended(
    api: httpx.AsyncClient,
) -> None:
    # The clock is deliberately not advanced: PostgreSQL's `now()` is identical for every
    # row written in one transaction, so three campaigns sharing a `created_at` is the case
    # the cursor carries an id for. A cursor on the timestamp alone loses a row here.
    for name in ("first", "second", "third"):
        await create(api, name=name)

    first = (await api.get(CAMPAIGNS, params={"limit": 2})).json()
    second = (await api.get(CAMPAIGNS, params={"limit": 2, "after": first["next_cursor"]})).json()

    seen = [campaign["name"] for campaign in first["campaigns"] + second["campaigns"]]
    assert sorted(seen) == ["first", "second", "third"]
    assert second["next_cursor"] is None


async def test_the_list_can_be_searched_by_name(api: httpx.AsyncClient) -> None:
    await create(api, name="Summer MX")
    await create(api, name="Winter BR")

    found = (await api.get(CAMPAIGNS, params={"q": "winter"})).json()

    assert [campaign["name"] for campaign in found["campaigns"]] == ["Winter BR"]


async def test_a_cursor_this_service_did_not_issue_is_refused_by_its_own_name(
    api: httpx.AsyncClient,
) -> None:
    answer = await api.get(CAMPAIGNS, params={"after": "not-a-cursor"})

    assert answer.status_code == 422
    assert [field["location"] for field in answer.json()["errors"]] == ["query.after"]


async def test_an_oversized_page_is_refused_rather_than_quietly_cut(
    api: httpx.AsyncClient,
) -> None:
    answer = await api.get(CAMPAIGNS, params={"limit": 1000})

    assert answer.status_code == 422
    assert [field["location"] for field in answer.json()["errors"]] == ["query.limit"]


async def test_refetching_moves_the_campaign_forward(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    created = await create(api)
    world.clock.advance(timedelta(minutes=5))
    tracker_id = KeitaroCampaignId(FIRST_CAMPAIGN_ID)
    world.admin.campaigns[tracker_id] = replace(
        world.admin.campaigns[tracker_id], name="Renamed in Keitaro"
    )

    answer = await api.post(f"{CAMPAIGNS}/{created['id']}/refetch")

    assert answer.status_code == 200
    assert answer.json()["name"] == "Renamed in Keitaro"
    assert answer.json()["synced_at"] > created["synced_at"]


async def test_repairing_finishes_a_campaign_whose_flows_never_landed(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    world.admin.fail_on("create_stream", RuntimeError("never mind"))
    with pytest.raises(RuntimeError):
        await api.post(CAMPAIGNS, json=MADE_UP)
    world.admin.failures.clear()
    half_built = (await api.get(CAMPAIGNS)).json()["campaigns"][0]
    assert half_built["setup_status"] == "needs_attention"

    answer = await api.post(f"{CAMPAIGNS}/{half_built['id']}/repair")

    assert answer.status_code == 200
    assert answer.json()["setup_status"] == "ready"
    assert sorted(flow.name for flow in world.admin.streams.values()) == [FIRST_FLOW, SECOND_FLOW]


async def test_a_campaign_this_service_has_never_heard_of_answers_404(
    api: httpx.AsyncClient,
) -> None:
    answer = await api.post(f"{CAMPAIGNS}/{uuid4()}/refetch")

    assert answer.status_code == 404
    assert answer.json()["code"] == "campaign-not-found"


async def test_an_identifier_that_is_not_one_is_refused_by_its_own_name(
    api: httpx.AsyncClient,
) -> None:
    answer = await api.post(f"{CAMPAIGNS}/not-a-uuid/repair")

    assert answer.status_code == 422
    assert [field["location"] for field in answer.json()["errors"]] == ["path.campaign_id"]
