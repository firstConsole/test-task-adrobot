"""How often the tracker is really asked for numbers, and what is shown when it will not say.

The rule this file exists for is the one that is invisible on a working screen: a column
that has lost its tracker must say so, and must not say "zero". Those two look identical in
a cell and mean opposite things to somebody deciding whether an offer is worth keeping.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from adrobot.application.errors import UpstreamDeniedError, UpstreamUnavailableError
from adrobot.application.statistics import (
    MAX_HELD_CAMPAIGNS,
    STATS_TTL,
    UNAVAILABLE,
    StatsReader,
)
from adrobot.domain.ids import KeitaroCampaignId, KeitaroStreamId, OfferId
from adrobot.domain.offer import OfferStats
from tests.fakes import FIRST_CAMPAIGN_ID, FakeClock, FakeKeitaroReports

CAMPAIGN = KeitaroCampaignId(FIRST_CAMPAIGN_ID)
ANOTHER = KeitaroCampaignId(FIRST_CAMPAIGN_ID + 1)

TODAYS_CLICKS = {564221: 2, 564222: 7}
TODAYS_OFFERS = {11112: OfferStats(clicks=4, conversions=1)}

ONE_REPORT_EACH = ["clicks_by_stream", "clicks_by_offer"]


def reader(
    *, timezone: str = "UTC", at: datetime = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
) -> tuple[StatsReader, FakeKeitaroReports, FakeClock]:
    reports = FakeKeitaroReports(clicks=TODAYS_CLICKS, stats=TODAYS_OFFERS)
    clock = FakeClock(at)
    return StatsReader(reports, clock, timezone=timezone), reports, clock


async def test_a_second_look_inside_the_window_costs_the_tracker_nothing() -> None:
    read, reports, clock = reader()

    first = await read.read(CAMPAIGN)
    clock.advance(STATS_TTL - timedelta(seconds=1))
    again = await read.read(CAMPAIGN)

    assert again == first
    assert reports.calls == ONE_REPORT_EACH


async def test_the_window_closes_and_the_next_look_asks_again() -> None:
    read, reports, clock = reader()

    await read.read(CAMPAIGN)
    clock.advance(STATS_TTL)
    refreshed = await read.read(CAMPAIGN)

    assert reports.calls == ONE_REPORT_EACH * 2
    assert refreshed.read_at == clock.at


async def test_one_campaigns_reading_is_never_served_for_another() -> None:
    read, reports, _ = reader()

    await read.read(CAMPAIGN)
    await read.read(ANOTHER)

    assert [campaign for campaign, _ in reports.asked] == [CAMPAIGN, CAMPAIGN, ANOTHER, ANOTHER]


async def test_a_reading_is_the_trackers_numbers_and_says_so() -> None:
    read, _, clock = reader()

    stats = await read.read(CAMPAIGN)

    assert stats.available
    assert not stats.stale
    assert stats.unavailable_reason is None
    assert stats.read_at == clock.at


async def test_a_tracker_that_will_not_answer_darkens_the_column_rather_than_emptying_it() -> None:
    read, reports, _ = reader()
    reports.failure = UpstreamUnavailableError("report/build timed out")

    stats = await read.read(CAMPAIGN)

    # Not an exception: the editor beside this column is working, and a failed report is a
    # cell that cannot be filled rather than a screen that cannot be drawn.
    assert not stats.available
    assert stats.unavailable_reason == UNAVAILABLE
    assert stats.streams == ()
    assert stats.offers == ()
    # Still dated and named, so the heading over the empty column is still true.
    assert stats.day.isoformat() == "2026-09-20"
    assert stats.timezone == "UTC"


async def test_the_reason_a_client_reads_is_ours_and_never_the_trackers() -> None:
    read, reports, _ = reader()
    # The one upstream failure whose own message names an environment variable. It renders
    # as the detail of a 502, which api/errors.py withholds outside dev; a 200 carrying it
    # in a field would be that disclosure through a door left open.
    reports.failure = UpstreamDeniedError("forbidden", status=403)

    stats = await read.read(CAMPAIGN)

    assert stats.unavailable_reason == UNAVAILABLE
    assert "ADROBOT_KEITARO_API_KEY" not in str(stats.unavailable_reason)


async def test_the_last_numbers_that_could_be_read_are_served_dated_rather_than_dropped() -> None:
    read, reports, clock = reader()
    first = await read.read(CAMPAIGN)
    clock.advance(STATS_TTL * 2)
    reports.failure = UpstreamUnavailableError("report/build timed out")

    stats = await read.read(CAMPAIGN)

    assert stats.streams == first.streams
    assert stats.offers == first.offers
    assert stats.available
    assert stats.stale
    assert stats.unavailable_reason == UNAVAILABLE
    # Dated when it was read and not when it was served, which is what makes showing it
    # honest: the screen prints the moment beside the number.
    assert stats.read_at == first.read_at


async def test_a_failure_does_not_replace_what_is_held_and_a_recovery_does() -> None:
    read, reports, clock = reader()
    await read.read(CAMPAIGN)
    clock.advance(STATS_TTL * 2)
    reports.failure = UpstreamUnavailableError("report/build timed out")
    await read.read(CAMPAIGN)

    reports.failure = None
    reports.clicks = {KeitaroStreamId(564221): 99}
    clock.advance(STATS_TTL)
    recovered = await read.read(CAMPAIGN)

    assert [row.clicks for row in recovered.streams] == [99]
    assert not recovered.stale
    assert recovered.unavailable_reason is None


async def test_yesterdays_clicks_are_never_served_under_todays_heading() -> None:
    read, reports, clock = reader(timezone="Australia/Sydney")
    yesterday = await read.read(CAMPAIGN)
    # Past midnight in Sydney, which is the boundary this cache is keyed against.
    clock.advance(timedelta(hours=3))
    reports.failure = UpstreamUnavailableError("report/build timed out")

    today = await read.read(CAMPAIGN)

    assert today.day > yesterday.day
    # The held reading is yesterday's, so the failure falls through to "no numbers" rather
    # than dressing yesterday's clicks up as this morning's.
    assert not today.available
    assert today.streams == ()


async def test_the_day_asked_for_is_the_trackers_and_not_this_machines() -> None:
    read, reports, _ = reader(
        timezone="Australia/Sydney", at=datetime(2026, 9, 20, 23, 30, tzinfo=UTC)
    )

    stats = await read.read(CAMPAIGN)

    assert stats.day.isoformat() == "2026-09-21"
    assert [day.isoformat() for _, day in reports.asked] == ["2026-09-21", "2026-09-21"]


async def test_the_cache_has_a_ceiling_and_does_not_grow_without_one() -> None:
    read, reports, _ = reader()

    for number in range(MAX_HELD_CAMPAIGNS + 1):
        await read.read(KeitaroCampaignId(FIRST_CAMPAIGN_ID + number))
    # The first campaign was dropped with the rest, so looking at it again reads the tracker
    # — which is the whole cost of the ceiling.
    before = len(reports.calls)
    await read.read(CAMPAIGN)

    assert len(reports.calls) == before + len(ONE_REPORT_EACH)


async def test_a_campaign_that_took_no_traffic_is_available_and_empty() -> None:
    read = StatsReader(FakeKeitaroReports(), FakeClock(), timezone="UTC")

    stats = await read.read(CAMPAIGN)

    # The distinction the whole degradation exists for, from the other side.
    assert stats.available
    assert stats.streams == ()
    assert stats.offers == ()


async def test_conversions_ride_along_with_the_clicks_they_were_read_beside() -> None:
    read, _, _ = reader()

    stats = await read.read(CAMPAIGN)

    # One report, two measures. A second request for the conversions would double the cost
    # of the most expensive endpoint the tracker has, for a column drawn beside this one.
    assert [(row.offer_id, row.clicks, row.conversions) for row in stats.offers] == [
        (OfferId(11112), 4, 1)
    ]
