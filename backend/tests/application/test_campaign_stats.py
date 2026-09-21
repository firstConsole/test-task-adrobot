"""The Stats column, against the campaign from the video.

Two things matter most here, and the share arithmetic is neither of them. One is that the
question reaches the tracker naming *this* campaign and the tracker's own day — a column
showing yesterday's clicks looks exactly like a column showing today's. The other is that
the two reports are the whole cost of the screen, and that no database connection is held
while they are in flight.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from adrobot.application.dto import OfferClicks, StreamClicks
from adrobot.application.errors import CampaignNotFoundError
from adrobot.application.statistics import StatsReader
from adrobot.application.time_zone import TrackerTimeZone
from adrobot.application.use_cases.campaign_stats import GetCampaignStats
from adrobot.domain.ids import CampaignId, KeitaroCampaignId, KeitaroStreamId
from adrobot.domain.offer import OfferStats
from tests.fake_persistence import ReportsThatWatchTheDatabase
from tests.fakes import FIRST_CAMPAIGN_ID, FIRST_STREAM_ID, REFERENCE_OFFERS, FakeKeitaroReports
from tests.helpers import given_mirrored_campaign
from tests.wiring import FakeWorld, fake_world

OLDEST, NEWEST = REFERENCE_OFFERS
ROTATING_FLOW = KeitaroStreamId(FIRST_STREAM_ID + 1)

TODAYS_CLICKS = {int(ROTATING_FLOW): 7, FIRST_STREAM_ID: 2}
TODAYS_OFFERS = {
    int(OLDEST): OfferStats(clicks=4, conversions=1),
    int(NEWEST): OfferStats(clicks=3),
}


def statistics(world: FakeWorld, *, timezone: str = "UTC") -> GetCampaignStats:
    """The scenario as `api/deps.py` assembles it, over this world's fakes."""
    zone = TrackerTimeZone(world.admin, configured=timezone)
    return GetCampaignStats(stats=StatsReader(world.reports, world.clock, zone=zone), uow=world.uow)


@pytest.fixture
def world() -> FakeWorld:
    return fake_world(reports=FakeKeitaroReports(clicks=TODAYS_CLICKS, stats=TODAYS_OFFERS))


async def test_it_answers_with_both_groupings_in_two_calls_to_the_tracker(
    world: FakeWorld,
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    stats = await statistics(world)(campaign_id)

    assert stats.streams == (
        StreamClicks(keitaro_stream_id=KeitaroStreamId(FIRST_STREAM_ID), clicks=2),
        StreamClicks(keitaro_stream_id=ROTATING_FLOW, clicks=7),
    )
    assert stats.offers == (
        OfferClicks(offer_id=OLDEST, clicks=4, conversions=1),
        OfferClicks(offer_id=NEWEST, clicks=3),
    )
    # The whole screen, however many flows and offers it draws.
    assert world.reports.calls == ["clicks_by_stream", "clicks_by_offer"]


async def test_the_rows_are_ordered_by_id_whatever_order_the_report_answered_in(
    world: FakeWorld,
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    stats = await statistics(world)(campaign_id)

    # The fixture lists the busier flow first, as a report sorted by clicks would. Ordering
    # by id instead is what makes two reads of one screen answer identically; sorting by
    # size would be this layer deciding how a column is sorted, and the table draws its rows
    # where `display_order` already put them.
    assert list(TODAYS_CLICKS) != sorted(TODAYS_CLICKS)
    assert [int(row.keitaro_stream_id) for row in stats.streams] == sorted(TODAYS_CLICKS)


async def test_it_asks_about_the_campaign_the_tracker_knows_and_not_about_our_own_id(
    world: FakeWorld,
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    await statistics(world)(campaign_id)

    asked = {campaign for campaign, _, _ in world.reports.asked}
    assert asked == {KeitaroCampaignId(FIRST_CAMPAIGN_ID)}


async def test_today_is_the_trackers_day_and_not_this_machines(world: FakeWorld) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)
    # Half past eleven at night in London is half past nine the next morning in Sydney, and
    # a tracker in Sydney has been filling tomorrow's report for nine hours by then.
    world.clock.at = datetime(2026, 9, 20, 23, 30, tzinfo=UTC)

    stats = await statistics(world, timezone="Australia/Sydney")(campaign_id)

    assert [day for _, day, _ in world.reports.asked] == [stats.day, stats.day]
    assert stats.day.isoformat() == "2026-09-21"
    # Printed next to the number, because "clicks today" is unverifiable without it.
    assert stats.timezone == "Australia/Sydney"
    assert stats.read_at == world.clock.at


async def test_a_day_that_has_not_started_in_the_trackers_zone_is_still_yesterday(
    world: FakeWorld,
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)
    world.clock.at = datetime(2026, 9, 21, 3, 0, tzinfo=UTC)

    stats = await statistics(world, timezone="America/Los_Angeles")(campaign_id)

    assert stats.day.isoformat() == "2026-09-20"


async def test_it_reads_no_report_for_a_campaign_this_service_has_never_opened(
    world: FakeWorld,
) -> None:
    with pytest.raises(CampaignNotFoundError):
        await statistics(world)(CampaignId(uuid4()))

    assert world.reports.calls == []


async def test_no_database_transaction_is_open_while_a_report_is_in_flight() -> None:
    world = fake_world()
    watching = ReportsThatWatchTheDatabase(world.uow, clicks=TODAYS_CLICKS, stats=TODAYS_OFFERS)
    campaign_id, _ = await given_mirrored_campaign(world)

    zone = TrackerTimeZone(world.admin, configured="UTC")
    stats = await GetCampaignStats(
        stats=StatsReader(watching, world.clock, zone=zone), uow=world.uow
    )(campaign_id)

    assert StreamClicks(keitaro_stream_id=ROTATING_FLOW, clicks=7) in stats.streams


async def test_a_campaign_that_took_no_traffic_today_answers_with_no_rows_at_all() -> None:
    world = fake_world(reports=FakeKeitaroReports())
    campaign_id, _ = await given_mirrored_campaign(world)

    stats = await statistics(world)(campaign_id)

    assert stats.streams == ()
    assert stats.offers == ()
    # Still a day and a zone: an empty column means "nothing today", and today is nameable.
    assert stats.day == world.clock.at.date()
    assert stats.timezone == "UTC"


async def test_an_offer_the_report_did_not_mention_gets_no_row_although_the_flow_holds_it() -> None:
    # Both offers are in flow 2; only one of them took a click today.
    world = fake_world(reports=FakeKeitaroReports(stats={int(NEWEST): OfferStats(clicks=3)}))
    campaign_id, _ = await given_mirrored_campaign(world)

    stats = await statistics(world)(campaign_id)

    # The report is the source here and the mirror is not consulted, so nothing invents a
    # zero for the silent row: the screen draws a blank cell, which is the difference
    # between "no clicks today" and "the tracker said nothing about this".
    assert [row.offer_id for row in stats.offers] == [NEWEST]
    assert OLDEST not in [row.offer_id for row in stats.offers]
