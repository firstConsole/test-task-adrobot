"""That the in-memory tracker behaves the way the port says a tracker behaves.

A fake nobody tests is a second source of truth about the system, and the bugs it hides are
the expensive kind: every scenario passes and the real adapter does something else. These
are the promises stages 6 and 7 will lean on.
"""

from __future__ import annotations

from datetime import date

import pytest

from adrobot.application.errors import UpstreamNotFoundError, UpstreamUnavailableError
from adrobot.domain.campaign import CampaignBlueprint
from adrobot.domain.diff import DesiredOffer
from adrobot.domain.ids import KeitaroCampaignId, KeitaroStreamId, OfferId
from adrobot.domain.offer import Offer, OfferStats
from adrobot.domain.stream import StreamSchema, StreamSpec, StreamType
from adrobot.domain.values import CampaignAlias, CampaignName, OfferState
from tests.fakes import FIRST_CAMPAIGN_ID, FIRST_STREAM_ID, FakeKeitaroAdmin, FakeKeitaroReports

A_BLUEPRINT = CampaignBlueprint(
    name=CampaignName("Summer MX"), alias=CampaignAlias("summer-mx"), group_id=7
)


def _spec(campaign_id: int, *offers: DesiredOffer) -> StreamSpec:
    return StreamSpec(
        campaign_id=KeitaroCampaignId(campaign_id),
        name="Flow 2",
        type=StreamType.REGULAR,
        schema=StreamSchema.LANDINGS,
        action_type="http",
        position=2,
        offers=offers,
    )


def _desired(offer_id: int, share: int, state: OfferState = OfferState.ACTIVE) -> DesiredOffer:
    return DesiredOffer(offer_id=OfferId(offer_id), share=share, state=state)


async def test_a_campaign_created_can_be_read_back() -> None:
    admin = FakeKeitaroAdmin()

    created = await admin.create_campaign(A_BLUEPRINT)

    assert created.id == FIRST_CAMPAIGN_ID, "the identifiers from the video, on purpose"
    assert await admin.get_campaign(created.id) == created


async def test_a_campaign_nobody_created_is_not_found() -> None:
    with pytest.raises(UpstreamNotFoundError):
        await FakeKeitaroAdmin().get_campaign(KeitaroCampaignId(1))


async def test_a_flow_belongs_to_its_campaign_and_comes_back_in_position_order() -> None:
    admin = FakeKeitaroAdmin()
    campaign = await admin.create_campaign(A_BLUEPRINT)
    second = await admin.create_stream(_spec(campaign.id))
    first = await admin.create_stream(
        StreamSpec(
            campaign_id=campaign.id,
            name="Flow 1",
            type=StreamType.REGULAR,
            schema=StreamSchema.REDIRECT,
            action_type="http",
            position=1,
        )
    )

    listed = await admin.list_campaign_streams(campaign.id)

    assert [flow.id for flow in listed] == [first.id, second.id]
    assert listed[0].id == FIRST_STREAM_ID + 1


async def test_a_push_leaves_the_flow_holding_what_was_asked_for() -> None:
    admin = FakeKeitaroAdmin()
    flow = await admin.create_stream(_spec(FIRST_CAMPAIGN_ID, _desired(3749, 100)))

    written = await admin.replace_stream_offers(flow.id, (_desired(3749, 50), _desired(11112, 50)))

    assert {row.offer_id: row.share for row in written.offers} == {3749: 50, 11112: 50}
    assert admin.streams[flow.id] == written, "and remembers it for the next read"


async def test_a_tracker_that_replaces_the_array_drops_the_removed_row() -> None:
    admin = FakeKeitaroAdmin(merges=False)
    flow = await admin.create_stream(
        _spec(FIRST_CAMPAIGN_ID, _desired(3749, 50), _desired(11112, 50))
    )

    written = await admin.replace_stream_offers(
        flow.id, (_desired(3749, 100), _desired(11112, 0, OfferState.DISABLED))
    )

    assert [row.offer_id for row in written.offers] == [3749]


async def test_a_tracker_that_merges_keeps_the_removed_row_switched_off() -> None:
    admin = FakeKeitaroAdmin(merges=True)
    flow = await admin.create_stream(
        _spec(FIRST_CAMPAIGN_ID, _desired(3749, 50), _desired(11112, 50))
    )

    written = await admin.replace_stream_offers(
        flow.id, (_desired(3749, 100), _desired(11112, 0, OfferState.DISABLED))
    )

    switched_off = written.offers[1]
    assert (switched_off.offer_id, switched_off.share, switched_off.state) == (
        11112,
        0,
        "disabled",
    ), "and either way the offer takes no traffic, which is the whole of what was asked"


async def test_a_row_that_survives_a_push_keeps_the_timestamp_the_tie_break_reads() -> None:
    admin = FakeKeitaroAdmin()
    flow = await admin.create_stream(_spec(FIRST_CAMPAIGN_ID, _desired(3749, 100)))
    was = flow.offers[0]

    written = await admin.replace_stream_offers(flow.id, (_desired(3749, 50), _desired(11112, 50)))

    kept, added = written.offers
    assert (kept.created_at, kept.row_id) == (was.created_at, was.row_id)
    assert added.created_at is not None
    assert kept.created_at is not None
    assert added.created_at > kept.created_at, (
        "the newer row is the one the rounding remainder goes to"
    )


async def test_a_flow_that_was_already_there_can_be_handed_to_the_editor() -> None:
    admin = FakeKeitaroAdmin()
    made_by_hand = await admin.create_stream(_spec(FIRST_CAMPAIGN_ID, _desired(3749, 25)))

    assert admin.given_stream(made_by_hand) is made_by_hand
    assert await admin.list_campaign_streams(KeitaroCampaignId(FIRST_CAMPAIGN_ID)) == (
        made_by_hand,
    )


async def test_a_push_to_a_flow_that_is_not_there_is_not_found() -> None:
    with pytest.raises(UpstreamNotFoundError, match="flow 1"):
        await FakeKeitaroAdmin().replace_stream_offers(KeitaroStreamId(1), ())


async def test_a_flow_written_whole_keeps_its_own_identity() -> None:
    admin = FakeKeitaroAdmin()
    flow = await admin.create_stream(_spec(FIRST_CAMPAIGN_ID, _desired(3749, 100)))

    written = await admin.update_stream(flow.id, _spec(FIRST_CAMPAIGN_ID, _desired(3749, 100)))

    assert written.id == flow.id


async def test_the_catalogues_are_whatever_the_test_put_there() -> None:
    admin = FakeKeitaroAdmin(offers=(Offer(id=OfferId(11112), name="Oxys", state="active"),))

    assert (await admin.list_offers())[0].name == "Oxys"
    assert (await admin.list_reference_data()).campaign_groups[0].name == "AD Robot"

    created = await admin.create_campaign_group("Another")

    assert (await admin.list_reference_data()).campaign_groups[-1] == created


async def test_a_named_call_can_be_made_to_fail() -> None:
    admin = FakeKeitaroAdmin()
    admin.fail_on("create_stream", UpstreamUnavailableError("the tracker fell over"))

    await admin.create_campaign(A_BLUEPRINT)
    with pytest.raises(UpstreamUnavailableError):
        await admin.create_stream(_spec(FIRST_CAMPAIGN_ID))

    # The shape of the partial failure 6.3 compensates for: a campaign in the tracker and
    # a flow that never was.
    assert admin.calls == ["create_campaign", "create_stream"]
    assert len(admin.campaigns) == 1
    assert admin.streams == {}


async def test_the_statistics_are_whatever_the_test_put_there() -> None:
    reports = FakeKeitaroReports(clicks={564221: 7}, stats={3749: OfferStats(clicks=7)})

    campaign, day = KeitaroCampaignId(93212), date(2026, 9, 20)

    assert await reports.clicks_by_stream(campaign, day, timezone="UTC") == {564221: 7}
    assert await reports.clicks_by_offer(campaign, day, timezone="UTC") == {
        3749: OfferStats(clicks=7)
    }
    assert reports.calls == ["clicks_by_stream", "clicks_by_offer"]
    # What it was asked, and not only which methods: a scenario reading the report for this
    # machine's date, or in this machine's zone, calls exactly the same two.
    assert reports.asked == [(campaign, day, "UTC"), (campaign, day, "UTC")]


async def test_the_report_builder_can_be_made_to_fall_over_on_its_own() -> None:
    reports = FakeKeitaroReports(clicks={564221: 7})
    reports.failure = UpstreamUnavailableError("report/build is down")

    campaign, day = KeitaroCampaignId(93212), date(2026, 9, 20)

    with pytest.raises(UpstreamUnavailableError):
        await reports.clicks_by_stream(campaign, day, timezone="UTC")
