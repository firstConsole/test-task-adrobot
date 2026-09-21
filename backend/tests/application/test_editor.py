"""The editor's read, against the campaign from the video.

What is asserted is what the screen prints: which flows, in which order, at which shares,
and which of the three buttons the server says may be pressed. The arithmetic itself is
`tests/domain/test_shares.py`'s business — these tests only prove that the answer reaches
the screen unspoilt and in the order a reviewer compares against Keitaro.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from adrobot.application.errors import CampaignNotFoundError
from adrobot.application.use_cases.editor import GetEditorView
from adrobot.domain.draft import DraftOperationKind
from adrobot.domain.ids import CampaignId, OfferId
from adrobot.domain.offer import Offer
from adrobot.domain.stream import StreamOffer, StreamSchema
from adrobot.domain.values import Share
from tests.fakes import FIRST_MOMENT, REFERENCE_OFFERS
from tests.helpers import given_mirrored_campaign, given_staged_draft, operation

if TYPE_CHECKING:
    from adrobot.application.dto import StreamEditorView
    from tests.wiring import FakeWorld

OLDEST, NEWEST = REFERENCE_OFFERS
ADDED = OfferId(11111)
ADD, REMOVE = DraftOperationKind.ADD, DraftOperationKind.REMOVE


def on_screen(flow: StreamEditorView) -> list[tuple[int, int, bool]]:
    """The offer table as it is drawn: id, share and whether the row is greyed out."""
    return [(int(row.offer_id), row.share, row.removed) for row in flow.rows]


async def test_both_flows_come_back_in_position_order_and_only_one_rotates_offers(
    world: FakeWorld,
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    view = await GetEditorView(uow=world.uow)(campaign_id)

    assert [flow.stream.name for flow in view.streams] == ["Flow 1", "Flow 2"]
    assert view.streams[0].stream.schema is StreamSchema.REDIRECT
    assert view.streams[0].rows == (), "a redirect flow has no offer table to draw"
    assert view.campaign.campaign.keitaro_campaign_id == 93212


async def test_a_clean_flow_shows_the_tracker_s_own_shares_and_offers_no_buttons(
    world: FakeWorld,
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    flow = (await GetEditorView(uow=world.uow)(campaign_id)).streams[1]

    assert on_screen(flow) == [(OLDEST, 25, False), (NEWEST, 25, False)], "50% is a real state"
    assert (flow.dirty, flow.can_push, flow.diff) == (False, False, None)
    assert flow.block_reason is None
    assert flow.warnings == ()


async def test_an_offer_the_catalogue_has_never_seen_still_gets_its_row(
    world: FakeWorld,
) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)
    async with world.uow.begin() as transaction:
        await transaction.offers.upsert_catalogue(
            (Offer(id=OLDEST, name="Oxys", state="active"),), at=FIRST_MOMENT
        )

    flow = (await GetEditorView(uow=world.uow)(campaign_id)).streams[1]

    labelled = {
        int(row.offer_id): None if row.offer is None else row.offer.name for row in flow.rows
    }
    assert labelled == {OLDEST: "Oxys", NEWEST: None}


async def test_removing_a_row_recalculates_the_rest_and_sinks_it_to_the_bottom(
    world: FakeWorld,
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(REMOVE, OLDEST))

    flow = (await GetEditorView(uow=world.uow)(campaign_id)).streams[1]

    assert on_screen(flow) == [(NEWEST, 100, False), (OLDEST, 0, True)]
    assert (flow.dirty, flow.can_push) == (True, True)
    assert flow.diff is not None
    assert flow.diff.removed == (OLDEST,)
    assert [(int(row.offer_id), row.was, row.now) for row in flow.diff.share_changes] == [
        (NEWEST, 25, 100)
    ]


async def test_an_added_offer_takes_the_rounding_remainder(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))

    flow = (await GetEditorView(uow=world.uow)(campaign_id)).streams[1]

    assert on_screen(flow) == [(ADDED, 34, False), (OLDEST, 33, False), (NEWEST, 33, False)]
    assert flow.diff is not None
    assert flow.diff.added == (ADDED,)


async def test_a_draft_that_would_leave_no_active_offer_says_why_push_is_dark(
    world: FakeWorld,
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(
        world, campaign_id, stream_id, operation(REMOVE, OLDEST), operation(REMOVE, NEWEST)
    )

    flow = (await GetEditorView(uow=world.uow)(campaign_id)).streams[1]

    assert flow.dirty, "there is still a draft to cancel"
    assert not flow.can_push
    assert flow.block_reason == (
        "Flow 2 would have no active offer left — its traffic would go nowhere"
    )


async def test_an_offer_added_and_taken_back_out_reaches_neither_the_summary_nor_the_payload(
    world: FakeWorld,
) -> None:
    """And the recalculation it set off stays, which is what makes the flow sum to 100.

    Opening a draft normalises nothing, so this flow was at 25+25; the first edit is what
    divides the whole, and undoing that edit does not undo the division. It is the reference
    tool's own behaviour — the sum reaches 100 on the first mutation and never before — and
    it is why PUSH is live here although the row that was added is gone again.
    """
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(
        world, campaign_id, stream_id, operation(ADD, ADDED), operation(REMOVE, ADDED)
    )

    flow = (await GetEditorView(uow=world.uow)(campaign_id)).streams[1]

    assert on_screen(flow) == [(OLDEST, 50, False), (NEWEST, 50, False), (ADDED, 0, True)]
    assert flow.diff is not None
    assert (flow.diff.added, flow.diff.removed) == ((), ()), "the tracker never heard of it"
    assert [int(row.offer_id) for row in flow.diff.desired] == [OLDEST, NEWEST]
    assert (flow.dirty, flow.can_push) == (True, True)


async def test_a_flow_fetched_since_the_draft_was_opened_carries_a_warning(
    world: FakeWorld,
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))

    async with world.uow.begin() as transaction:
        # FETCH STREAMS FROM KT, landing on a flow somebody has since edited in Keitaro.
        await transaction.streams.upsert_stream_offers(
            campaign_id=campaign_id,
            stream_id=stream_id,
            offers=(
                StreamOffer(
                    offer_id=OLDEST, share=60, state="active", row_id=1, created_at=FIRST_MOMENT
                ),
            ),
            at=FIRST_MOMENT,
        )

    flow = (await GetEditorView(uow=world.uow)(campaign_id)).streams[1]

    assert flow.warnings == (
        (
            "this flow has been fetched from Keitaro since the draft was opened — "
            "pushing will overwrite what the tracker holds now"
        ),
    )
    assert flow.can_push, "our own cache moving is not a reason to refuse the button"


async def test_a_pin_shows_on_the_row_and_moves_nothing(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    async with world.uow.begin() as transaction:
        await transaction.pins.hold(
            campaign_id=campaign_id, stream_id=stream_id, offer_id=NEWEST, share=Share(40)
        )

    flow = (await GetEditorView(uow=world.uow)(campaign_id)).streams[1]

    assert {int(row.offer_id): row.pinned_share for row in flow.rows} == {OLDEST: None, NEWEST: 40}
    assert on_screen(flow) == [(OLDEST, 25, False), (NEWEST, 25, False)]
    assert not flow.dirty, "a pin is not an edit"


async def test_a_campaign_nobody_opened_here_is_not_found(world: FakeWorld) -> None:
    with pytest.raises(CampaignNotFoundError):
        await GetEditorView(uow=world.uow)(CampaignId(uuid4()))


async def test_the_whole_screen_is_read_in_one_transaction(world: FakeWorld) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)
    world.uow.blocks = 0

    await GetEditorView(uow=world.uow)(campaign_id)

    assert world.uow.blocks == 1
