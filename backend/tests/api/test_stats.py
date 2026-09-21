"""The Stats endpoint, driven the way the editor screen drives it.

One request per screen, whatever it draws, and one answer that says how much of it to
believe. The assertions worth reading twice are the two about failure: a tracker that will
not build a report answers **200 with `available: false`**, because the campaign is there
and only the numbers are missing — and a column of zeroes would be this service telling a
media buyer an offer took no traffic when nobody was ever asked.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from adrobot.application.errors import UpstreamUnavailableError
from adrobot.application.statistics import STATS_TTL, UNAVAILABLE
from adrobot.domain.ids import CampaignId, OfferId
from adrobot.domain.offer import OfferStats
from tests.fakes import FIRST_STREAM_ID, REFERENCE_OFFERS, FakeKeitaroReports
from tests.helpers import VALID_ENVIRONMENT, given_mirrored_campaign
from tests.wiring import FakeWorld, fake_world

if TYPE_CHECKING:
    import httpx

TOKEN = VALID_ENVIRONMENT["ADROBOT_ACCESS_TOKEN"]
OLDEST, NEWEST = REFERENCE_OFFERS
ROTATING_FLOW = FIRST_STREAM_ID + 1

TODAYS_CLICKS = {ROTATING_FLOW: 7, FIRST_STREAM_ID: 2}
TODAYS_OFFERS = {
    int(OLDEST): OfferStats(clicks=4, conversions=1),
    int(NEWEST): OfferStats(clicks=3),
}


@pytest.fixture
def world() -> FakeWorld:
    """This module's application, with a tracker that has numbers to report."""
    return fake_world(reports=FakeKeitaroReports(clicks=TODAYS_CLICKS, stats=TODAYS_OFFERS))


@pytest.fixture
def api(client: httpx.AsyncClient) -> httpx.AsyncClient:
    """The application's client, carrying the shared token the way every caller must."""
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    return client


def stats_of(campaign_id: CampaignId) -> str:
    return f"/api/v1/campaigns/{campaign_id}/stats"


async def read(api: httpx.AsyncClient, campaign_id: CampaignId) -> dict[str, Any]:
    answer = await api.get(stats_of(campaign_id))
    assert answer.status_code == 200, answer.text
    return dict(answer.json())


async def test_one_request_answers_with_both_groupings_and_the_caption_over_them(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    body = await read(api, campaign_id)

    assert body["streams"] == [
        {"keitaro_stream_id": FIRST_STREAM_ID, "clicks": 2},
        {"keitaro_stream_id": ROTATING_FLOW, "clicks": 7},
    ]
    assert body["offers"] == [
        {"offer_id": int(OLDEST), "clicks": 4, "conversions": 1},
        {"offer_id": int(NEWEST), "clicks": 3, "conversions": 0},
    ]
    assert body["available"] is True
    assert body["stale"] is False
    assert body["unavailable_reason"] is None
    # The caption: "clicks today" is not checkable without both of these beside it.
    assert body["day"] == world.clock.at.date().isoformat()
    assert body["timezone"] == "UTC"
    assert body["read_at"] is not None


async def test_the_whole_screen_costs_the_tracker_two_reports(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    await read(api, campaign_id)

    assert world.reports.calls == ["clicks_by_stream", "clicks_by_offer"]


async def test_a_redraw_inside_the_window_reaches_no_further_than_this_process(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    first = await read(api, campaign_id)
    world.clock.advance(STATS_TTL - timedelta(seconds=1))
    again = await read(api, campaign_id)

    # Two requests, one round of reports: the reader belongs to the process and not to the
    # request, which is the whole reason it is on `AppPorts` and not built in `deps.py`.
    assert world.reports.calls == ["clicks_by_stream", "clicks_by_offer"]
    assert again == first


async def test_a_tracker_that_will_not_build_a_report_answers_200_and_says_so(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)
    world.reports.failure = UpstreamUnavailableError("report/build timed out")

    body = await read(api, campaign_id)

    # Not a 502. The editor beside this column is working, and a failed report is a cell
    # that cannot be filled rather than a screen that cannot be drawn.
    assert body["available"] is False
    assert body["unavailable_reason"] == UNAVAILABLE
    assert body["read_at"] is None
    assert body["streams"] == []
    assert body["offers"] == []
    # Still captioned, so the heading over the empty column is still true.
    assert body["day"] == world.clock.at.date().isoformat()


async def test_the_last_numbers_that_could_be_read_come_back_dated_and_flagged(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)
    first = await read(api, campaign_id)
    world.clock.advance(STATS_TTL * 2)
    world.reports.failure = UpstreamUnavailableError("report/build timed out")

    body = await read(api, campaign_id)

    assert body["available"] is True
    assert body["stale"] is True
    assert body["unavailable_reason"] == UNAVAILABLE
    assert body["streams"] == first["streams"]
    # Dated when it was read, not when it was served: that is what the screen prints.
    assert body["read_at"] == first["read_at"]


async def test_a_campaign_this_service_never_opened_is_a_404_and_not_an_empty_column(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    answer = await api.get(stats_of(CampaignId(uuid4())))

    assert answer.status_code == 404
    assert answer.headers["content-type"].startswith("application/problem+json")
    assert answer.json()["code"] == "campaign-not-found"
    # The campaign is the part that is missing here, so the tracker is never asked.
    assert world.reports.calls == []


async def test_the_numbers_need_the_shared_token_like_every_other_endpoint(
    client: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    answer = await client.get(stats_of(campaign_id))

    assert answer.status_code == 401
    assert answer.headers["WWW-Authenticate"] == "Bearer"


async def test_an_offer_the_report_did_not_mention_has_no_row_rather_than_a_zero(
    api: httpx.AsyncClient, world: FakeWorld
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)
    world.reports.stats = {OfferId(NEWEST): OfferStats(clicks=3)}

    body = await read(api, campaign_id)

    assert [row["offer_id"] for row in body["offers"]] == [int(NEWEST)]
