"""The editor endpoints, driven the way curl and the frontend drive them.

These are the requests a reviewer makes by hand after opening campaign 93212: look at the
flows, add an offer, look again, push, look again. What is asserted is the body they read
and the state the tracker is left in.

One assertion runs through all of them and is the point of the file: **no endpoint here
accepts a share.** A client says which offer to add or take out; the percentages come back
computed. The only number it may send is a pin, and that one is validated server-side.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

from adrobot.domain.ids import KeitaroStreamId, OfferId
from adrobot.domain.offer import Offer
from tests.fakes import FIRST_CAMPAIGN_ID, FIRST_STREAM_ID, REFERENCE_OFFERS
from tests.helpers import VALID_ENVIRONMENT, given_mirrored_campaign

if TYPE_CHECKING:
    import httpx

    from adrobot.domain.ids import CampaignId
    from tests.wiring import FakeWorld

TOKEN = VALID_ENVIRONMENT["ADROBOT_ACCESS_TOKEN"]
TRACKER = VALID_ENVIRONMENT["ADROBOT_KEITARO_PUBLIC_BASE_URL"]
OLDEST, NEWEST = REFERENCE_OFFERS
ADDED = OfferId(11111)
REDIRECT_FLOW = KeitaroStreamId(FIRST_STREAM_ID)


@pytest.fixture
def api(client: httpx.AsyncClient) -> httpx.AsyncClient:
    """The application's client, carrying the shared token the way every caller must."""
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    return client


def streams_of(campaign_id: CampaignId) -> str:
    return f"/api/v1/campaigns/{campaign_id}/streams"


def draft_of(campaign_id: CampaignId, stream_id: KeitaroStreamId) -> str:
    return f"{streams_of(campaign_id)}/{stream_id}/draft"


async def read(api: httpx.AsyncClient, campaign_id: CampaignId) -> dict[str, Any]:
    answer = await api.get(streams_of(campaign_id))
    assert answer.status_code == 200, answer.text
    return dict(answer.json())


def rotating(body: dict[str, Any]) -> dict[str, Any]:
    """The flow that rotates offers, which is the only one part 2 is about."""
    return next(flow for flow in body["streams"] if flow["schema"] == "landings")


def drawn(flow: dict[str, Any]) -> dict[int, int]:
    return {row["offer_id"]: row["share"] for row in flow["rows"]}


async def stage(
    api: httpx.AsyncClient,
    campaign_id: CampaignId,
    stream_id: KeitaroStreamId,
    *operations: tuple[str, int],
) -> dict[str, Any]:
    answer = await api.post(
        f"{draft_of(campaign_id, stream_id)}/operations",
        json={"operations": [{"kind": kind, "offer_id": offer} for kind, offer in operations]},
    )
    assert answer.status_code == 200, answer.text
    return dict(answer.json())


# --- reading the screen -------------------------------------------------------------------


async def test_the_screen_carries_the_campaign_and_both_flows_in_position_order(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    body = await read(api, campaign_id)

    assert body["campaign"]["keitaro_campaign_id"] == FIRST_CAMPAIGN_ID
    assert [flow["name"] for flow in body["streams"]] == ["Flow 1", "Flow 2"]
    assert body["streams"][0]["schema"] == "redirect", "the tracker's own word, on our wire too"
    assert body["streams"][0]["rows"] == [], "a redirect flow draws no offer table"


async def test_a_clean_flow_shows_the_trackers_own_shares_and_offers_no_buttons(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    flow = rotating(await read(api, campaign_id))

    assert drawn(flow) == {OLDEST: 25, NEWEST: 25}, "50% is a real clean state"
    assert (flow["dirty"], flow["can_push"], flow["diff"]) == (False, False, None)
    assert flow["filters"] == []
    assert flow["warnings"] == []


async def test_a_row_is_labelled_from_the_catalogue_with_a_preview_link(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)
    world.admin.offers = [
        Offer(
            id=OLDEST,
            name="Oxys",
            state="active",
            country=("pl", "-"),
            preview_path="/preview/1",
        )
    ]
    assert (await api.post("/api/v1/offers/sync")).status_code == 200

    flow = rotating(await read(api, campaign_id))

    labelled = next(row for row in flow["rows"] if row["offer_id"] == OLDEST)
    assert labelled["offer"] == {
        "id": OLDEST,
        "name": "Oxys",
        "state": "active",
        "country": ["pl", "-"],
        "affiliate_network": None,
        "preview_url": f"{TRACKER}/preview/1",
    }
    assert next(row for row in flow["rows"] if row["offer_id"] == NEWEST)["offer"] is None


async def test_another_campaign_s_screen_is_a_problem_document(api: httpx.AsyncClient) -> None:
    answer = await api.get("/api/v1/campaigns/00000000-0000-0000-0000-000000000000/streams")

    assert answer.status_code == 404
    assert answer.headers["content-type"].startswith("application/problem+json")
    assert answer.json()["code"] == "campaign-not-found"


# --- staging edits ------------------------------------------------------------------------


async def test_adding_an_offer_answers_with_the_recalculated_flow(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    flow = await stage(api, campaign_id, stream_id, ("add", ADDED))

    assert drawn(flow) == {ADDED: 34, OLDEST: 33, NEWEST: 33}
    assert (flow["dirty"], flow["can_push"]) == (True, True)
    assert flow["diff"]["added"] == [ADDED]


async def test_the_body_is_what_a_reload_would_fetch(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    staged = await stage(api, campaign_id, stream_id, ("add", ADDED))

    assert staged == rotating(await read(api, campaign_id))


async def test_a_refused_operation_answers_409_and_changes_nothing(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    answer = await api.post(
        f"{draft_of(campaign_id, stream_id)}/operations",
        json={
            "operations": [
                {"kind": "add", "offer_id": ADDED},
                {"kind": "add", "offer_id": ADDED},
            ]
        },
    )

    assert answer.status_code == 409
    assert answer.json()["code"] == "offer-already-in-flow"
    assert not rotating(await read(api, campaign_id))["dirty"], "the whole batch rolled back"


async def test_editing_a_redirect_flow_is_refused_by_name(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    answer = await api.post(
        f"{draft_of(campaign_id, REDIRECT_FLOW)}/operations",
        json={"operations": [{"kind": "add", "offer_id": ADDED}]},
    )

    assert answer.status_code == 409
    assert answer.json()["code"] == "flow-rotates-no-offers"


@pytest.mark.parametrize(
    "body",
    [
        {"operations": []},
        {"operations": [{"kind": "reshuffle", "offer_id": 1}]},
        {"operations": [{"kind": "add", "offer_id": ADDED, "share": 40}]},
        {"operations": [{"kind": "add", "offer_id": ADDED}], "force": True},
    ],
    ids=["empty-batch", "unknown-verb", "a-share-on-an-edit", "an-unknown-field"],
)
async def test_a_body_this_endpoint_does_not_take_is_refused(
    api: httpx.AsyncClient, world: FakeWorld, body: dict[str, Any]
) -> None:
    """`a-share-on-an-edit` is the one that matters: there is no way to send a percentage."""
    campaign_id, stream_id = await given_mirrored_campaign(world)

    answer = await api.post(f"{draft_of(campaign_id, stream_id)}/operations", json=body)

    assert answer.status_code == 422, answer.text
    assert answer.json()["code"] == "invalid-request"


# --- the pin ------------------------------------------------------------------------------


async def test_pinning_moves_no_share_and_lights_no_button(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    answer = await api.put(
        f"{streams_of(campaign_id)}/{stream_id}/offers/{NEWEST}/pin", json={"share": 25}
    )

    assert answer.status_code == 200, answer.text
    flow = answer.json()
    assert drawn(flow) == {OLDEST: 25, NEWEST: 25}
    assert {row["offer_id"]: row["pinned_share"] for row in flow["rows"]} == {
        OLDEST: None,
        NEWEST: 25,
    }
    assert not flow["dirty"]


async def test_a_pin_outside_the_range_is_refused_under_its_own_field(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    answer = await api.put(
        f"{streams_of(campaign_id)}/{stream_id}/offers/{NEWEST}/pin", json={"share": 140}
    )

    assert answer.status_code == 422
    assert [error["location"] for error in answer.json()["errors"]] == ["body.share"]


async def test_unpinning_a_row_nobody_pinned_is_not_an_error(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    answer = await api.delete(f"{streams_of(campaign_id)}/{stream_id}/offers/{NEWEST}/pin")

    assert answer.status_code == 200, answer.text


# --- preview, push and cancel -------------------------------------------------------------


async def test_the_preview_carries_the_exact_payload_the_push_would_send(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await stage(api, campaign_id, stream_id, ("remove", OLDEST))

    answer = await api.get(f"{draft_of(campaign_id, stream_id)}/preview")

    assert answer.status_code == 200, answer.text
    assert answer.json()["diff"]["desired"] == [
        {"offer_id": OLDEST, "share": 0, "state": "disabled"},
        {"offer_id": NEWEST, "share": 100, "state": "active"},
    ]


async def test_pushing_leaves_the_tracker_holding_what_the_screen_showed(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    staged = await stage(api, campaign_id, stream_id, ("add", ADDED))

    answer = await api.post(f"{draft_of(campaign_id, stream_id)}/push", json={})

    assert answer.status_code == 200, answer.text
    assert not answer.json()["dirty"]
    assert {int(row.offer_id): row.share for row in world.admin.streams[stream_id].offers} == drawn(
        staged
    )


async def test_a_flow_edited_in_keitaro_answers_409_with_both_states(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await stage(api, campaign_id, stream_id, ("add", ADDED))
    held, other = world.admin.streams[stream_id].offers
    world.admin.given_stream(
        replace(
            world.admin.streams[stream_id],
            offers=(replace(held, share=70), replace(other, share=30)),
        )
    )

    answer = await api.post(f"{draft_of(campaign_id, stream_id)}/push", json={})

    assert answer.status_code == 409
    body = answer.json()
    assert body["code"] == "draft-conflict"
    assert body["conflict"]["tracker_holds"] == [
        {"offer_id": OLDEST, "share": 70, "state": "active"},
        {"offer_id": NEWEST, "share": 30, "state": "active"},
    ]
    assert {row["offer_id"] for row in body["conflict"]["push_would_write"]} == {
        OLDEST,
        NEWEST,
        ADDED,
    }

    overwritten = await api.post(
        f"{draft_of(campaign_id, stream_id)}/push", json={"overwrite": True}
    )
    assert overwritten.status_code == 200, overwritten.text


async def test_cancelling_puts_the_trackers_own_shares_back(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await stage(api, campaign_id, stream_id, ("add", ADDED))

    answer = await api.delete(draft_of(campaign_id, stream_id))

    assert answer.status_code == 200, answer.text
    # The added row is gone rather than greyed: the mirror never had it, so there is no
    # tombstone to draw. A row the TRACKER once had would still be there, at 0%.
    assert drawn(answer.json()) == {OLDEST: 25, NEWEST: 25}


# --- the catalogue ------------------------------------------------------------------------


async def test_the_catalogue_is_searched_by_id_prefix_and_by_name(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    world.admin.offers = [
        Offer(id=OfferId(11104), name="Oxys", state="active"),
        Offer(id=OfferId(11234), name="11104 Special", state="active"),
    ]
    synced = await api.post("/api/v1/offers/sync")
    assert synced.json()["offers"] == 2

    found = await api.get("/api/v1/offers", params={"q": "11104"})

    assert [offer["id"] for offer in found.json()["offers"]] == [11104, 11234]


async def test_an_unknown_query_parameter_is_refused(api: httpx.AsyncClient) -> None:
    answer = await api.get("/api/v1/offers", params={"search": "oxys"})

    assert answer.status_code == 422
