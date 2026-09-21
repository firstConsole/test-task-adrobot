"""The pin, which is the one button that looks broken and is not.

Its whole behaviour is negative — it moves no share, opens no draft and lights no button —
so almost every test here asserts that something did *not* happen. The one that asserts
something did is the last: the video's fourth state, reached by pressing the editor's own
buttons in the order the video presses them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from adrobot.application.errors import StreamDoesNotRotateOffersError, StreamNotFoundError
from adrobot.application.use_cases.editor import GetEditorView
from adrobot.application.use_cases.pin_offer import ReleaseOfferPin, SetOfferPin
from adrobot.domain.draft import DraftOperationKind
from adrobot.domain.errors import OfferNotInStreamError, PinnedSharesExceedTotalError
from adrobot.domain.ids import CampaignId, KeitaroStreamId, OfferId
from adrobot.domain.values import Share
from tests.fakes import FIRST_STREAM_ID, REFERENCE_OFFERS
from tests.helpers import given_mirrored_campaign, given_staged_draft, operation

if TYPE_CHECKING:
    from adrobot.application.dto import StreamEditorView
    from tests.wiring import FakeWorld

OLDEST, NEWEST = REFERENCE_OFFERS
ADDED = OfferId(11111)
ABSENT = OfferId(999_999)
REDIRECT_FLOW = KeitaroStreamId(FIRST_STREAM_ID)
ADD, REMOVE, BRING_BACK = (
    DraftOperationKind.ADD,
    DraftOperationKind.REMOVE,
    DraftOperationKind.BRING_BACK,
)


def drawn(flow: StreamEditorView) -> dict[int, int]:
    """Offer id to share, as the screen prints it — a dict and never a tuple.

    The wrong tie-break rule produces the same multiset of values; only the pairing tells
    them apart, and the pairing is what a reviewer sees with Keitaro open beside the screen.
    """
    return {int(row.offer_id): row.share for row in flow.rows}


def pinned(flow: StreamEditorView) -> dict[int, int | None]:
    return {int(row.offer_id): row.pinned_share for row in flow.rows}


# --- what a pin does not do --------------------------------------------------------------


async def test_pinning_moves_no_share_and_opens_no_draft(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    flow = await SetOfferPin(uow=world.uow)(
        campaign_id=campaign_id, stream_id=stream_id, offer_id=NEWEST, share=Share(40)
    )

    assert drawn(flow) == {OLDEST: 25, NEWEST: 25}, "the tracker's own numbers, untouched"
    assert pinned(flow) == {OLDEST: None, NEWEST: 40}
    assert (flow.dirty, flow.can_push) == (False, False), "a pin is not an edit"


async def test_a_pin_with_no_number_holds_the_row_where_it_is(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    flow = await SetOfferPin(uow=world.uow)(
        campaign_id=campaign_id, stream_id=stream_id, offer_id=OLDEST
    )

    assert pinned(flow)[OLDEST] == 25


async def test_releasing_a_pin_nobody_holds_is_not_an_error(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    flow = await ReleaseOfferPin(uow=world.uow)(
        campaign_id=campaign_id, stream_id=stream_id, offer_id=NEWEST
    )

    assert pinned(flow) == {OLDEST: None, NEWEST: None}


async def test_one_transaction_and_never_a_word_to_the_tracker(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    world.uow.blocks = 0
    world.admin.calls.clear()

    await SetOfferPin(uow=world.uow)(
        campaign_id=campaign_id, stream_id=stream_id, offer_id=NEWEST, share=Share(40)
    )

    assert world.uow.blocks == 1
    assert world.admin.calls == []


# --- what a pin is refused for -----------------------------------------------------------


async def test_a_pin_on_a_row_the_flow_does_not_carry_is_refused(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    with pytest.raises(OfferNotInStreamError):
        await SetOfferPin(uow=world.uow)(
            campaign_id=campaign_id, stream_id=stream_id, offer_id=ABSENT, share=Share(40)
        )


async def test_pins_that_reserve_more_than_the_whole_are_refused(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await SetOfferPin(uow=world.uow)(
        campaign_id=campaign_id, stream_id=stream_id, offer_id=OLDEST, share=Share(60)
    )

    with pytest.raises(PinnedSharesExceedTotalError, match="110"):
        await SetOfferPin(uow=world.uow)(
            campaign_id=campaign_id, stream_id=stream_id, offer_id=NEWEST, share=Share(50)
        )

    kept = (await GetEditorView(uow=world.uow)(campaign_id)).streams[1]
    assert pinned(kept) == {OLDEST: 60, NEWEST: None}, "the refused pin was never written"


async def test_a_redirect_flow_has_no_rows_to_pin(world: FakeWorld) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    with pytest.raises(StreamDoesNotRotateOffersError):
        await SetOfferPin(uow=world.uow)(
            campaign_id=campaign_id, stream_id=REDIRECT_FLOW, offer_id=OLDEST, share=Share(40)
        )


async def test_another_campaign_s_flow_is_not_found(world: FakeWorld) -> None:
    _, stream_id = await given_mirrored_campaign(world)

    with pytest.raises(StreamNotFoundError):
        await SetOfferPin(uow=world.uow)(
            campaign_id=CampaignId(uuid4()), stream_id=stream_id, offer_id=OLDEST, share=Share(40)
        )


# --- what a pin does at the next edit ----------------------------------------------------


async def test_a_released_pin_puts_its_row_back_into_the_division(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await SetOfferPin(uow=world.uow)(
        campaign_id=campaign_id, stream_id=stream_id, offer_id=NEWEST, share=Share(25)
    )
    await ReleaseOfferPin(uow=world.uow)(
        campaign_id=campaign_id, stream_id=stream_id, offer_id=NEWEST
    )

    flow = await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))

    assert drawn(flow) == {ADDED: 34, OLDEST: 33, NEWEST: 33}


async def test_the_videos_fourth_state_through_the_editors_own_buttons(
    world: FakeWorld,
) -> None:
    """Pin one row at 25, take another out, bring it back: 38 / 37 / 25.

    The numbers from the video, and the state that picks the tie-break rule out of three
    candidates. `seq ASC` hands the 38 to the row that never left; the reference tool hands
    it to the one that came back, and so does this. Asserted as a dict, because all three
    candidate rules produce the same three values.
    """
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    await SetOfferPin(uow=world.uow)(
        campaign_id=campaign_id, stream_id=stream_id, offer_id=NEWEST, share=Share(25)
    )

    flow = await given_staged_draft(
        world,
        campaign_id,
        stream_id,
        operation(REMOVE, OLDEST),
        operation(BRING_BACK, OLDEST),
    )

    assert drawn(flow) == {OLDEST: 38, ADDED: 37, NEWEST: 25}
    assert pinned(flow)[NEWEST] == 25


async def test_a_pin_holds_its_row_while_the_rest_are_recalculated(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await SetOfferPin(uow=world.uow)(
        campaign_id=campaign_id, stream_id=stream_id, offer_id=NEWEST, share=Share(25)
    )

    flow = await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))

    assert drawn(flow) == {ADDED: 38, OLDEST: 37, NEWEST: 25}
