"""ADD, REMOVE, BRING BACK and CANCEL, against the campaign from the video.

The arithmetic itself belongs to `tests/domain/`; what is asserted here is the part only a
scenario can be held to — when a draft is opened and what it is seeded with, that a batch
is all or nothing, that the flow a client is answered with is the flow a reload would
fetch, and what CANCEL does and does not take with it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from adrobot.application.errors import (
    DraftBeingPushedError,
    StreamDoesNotRotateOffersError,
    StreamNotFoundError,
)
from adrobot.application.use_cases.edit_draft import DiscardDraft, EditDraft
from adrobot.application.use_cases.editor import GetEditorView
from adrobot.application.use_cases.pin_offer import SetOfferPin
from adrobot.domain.diff import snapshot_hash
from adrobot.domain.draft import DraftOperationKind, DraftStatus
from adrobot.domain.errors import OfferAlreadyInStreamError, OfferNotRemovedError
from adrobot.domain.ids import CampaignId, KeitaroStreamId, OfferId
from adrobot.domain.values import Share
from tests.fakes import FIRST_STREAM_ID, REFERENCE_OFFERS
from tests.helpers import (
    given_mirrored_campaign,
    given_staged_draft,
    locked_view,
    operation,
    shares,
)

if TYPE_CHECKING:
    from adrobot.application.dto import StreamEditorView
    from tests.wiring import FakeWorld

OLDEST, NEWEST = REFERENCE_OFFERS
ADDED = OfferId(11111)
REDIRECT_FLOW = KeitaroStreamId(FIRST_STREAM_ID)
ADD, REMOVE, BRING_BACK = (
    DraftOperationKind.ADD,
    DraftOperationKind.REMOVE,
    DraftOperationKind.BRING_BACK,
)


def on_screen(flow: StreamEditorView) -> list[tuple[int, int, bool]]:
    """The offer table as it is drawn: id, share and whether the row is greyed out."""
    return [(int(row.offer_id), row.share, row.removed) for row in flow.rows]


# --- opening a draft ---------------------------------------------------------------------


async def test_the_first_edit_opens_a_draft_seeded_from_the_mirror_as_it_stands(
    world: FakeWorld,
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    before = await locked_view(world.uow, campaign_id, stream_id)
    assert before.draft is None

    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))

    after = await locked_view(world.uow, campaign_id, stream_id)
    assert after.draft is not None
    assert after.draft.base_snapshot_hash == snapshot_hash(before.mirror_rows), (
        "the baseline is the flow the draft was opened on, never the edited rows"
    )


async def test_a_second_edit_continues_the_same_draft(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    opened = await locked_view(world.uow, campaign_id, stream_id)
    assert opened.draft is not None

    flow = await given_staged_draft(world, campaign_id, stream_id, operation(REMOVE, NEWEST))

    continued = await locked_view(world.uow, campaign_id, stream_id)
    assert continued.draft is not None
    assert continued.draft.id == opened.draft.id
    assert continued.draft.base_snapshot_hash == opened.draft.base_snapshot_hash
    # Two rows at 50 tie on share, so insertion order decides — which is why
    # `display_order` carries `seq` and the older row stays above the one just added.
    assert on_screen(flow) == [(OLDEST, 50, False), (ADDED, 50, False), (NEWEST, 0, True)]


async def test_an_empty_batch_opens_nothing(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    flow = await given_staged_draft(world, campaign_id, stream_id)

    assert not flow.dirty, "PUSH must not light up on a flow nobody touched"
    assert (await locked_view(world.uow, campaign_id, stream_id)).draft is None


# --- the operations ----------------------------------------------------------------------


async def test_a_row_brought_back_takes_the_rounding_remainder(world: FakeWorld) -> None:
    """State four from the video: the row activated last is the one that gets the extra unit.

    The rule `seq ASC` suggests itself and would hand the 34 to the offer that has been in
    the flow all along. Keitaro shows the other answer, and so does this.
    """
    campaign_id, stream_id = await given_mirrored_campaign(world)

    flow = await given_staged_draft(
        world,
        campaign_id,
        stream_id,
        operation(ADD, ADDED),
        operation(REMOVE, OLDEST),
        operation(BRING_BACK, OLDEST),
    )

    assert on_screen(flow) == [(OLDEST, 34, False), (NEWEST, 33, False), (ADDED, 33, False)]
    assert flow.diff is not None
    assert flow.diff.added == (ADDED,), "a row removed and returned inside one draft never left"


async def test_a_removed_row_stays_on_screen_at_zero_with_a_way_back(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    flow = await given_staged_draft(world, campaign_id, stream_id, operation(REMOVE, OLDEST))

    assert on_screen(flow) == [(NEWEST, 100, False), (OLDEST, 0, True)]
    assert flow.diff is not None
    assert flow.diff.removed == (OLDEST,)
    assert [(int(row.offer_id), row.share, row.state.value) for row in flow.diff.desired] == [
        (OLDEST, 0, "disabled"),
        (NEWEST, 100, "active"),
    ], "the removed row travels to the tracker explicitly, which is right under either semantics"


# --- a batch is all or nothing -----------------------------------------------------------


async def test_an_operation_the_domain_refuses_rolls_the_whole_batch_back(
    world: FakeWorld,
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    with pytest.raises(OfferAlreadyInStreamError):
        await given_staged_draft(
            world,
            campaign_id,
            stream_id,
            operation(ADD, ADDED),
            operation(ADD, ADDED),
        )

    after = await locked_view(world.uow, campaign_id, stream_id)
    assert after.draft is None, "the draft the first operation opened went back with it"
    assert shares(after.mirror_rows) == {OLDEST: 25, NEWEST: 25}


async def test_bringing_back_a_row_that_was_never_out_is_refused(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    with pytest.raises(OfferNotRemovedError):
        await given_staged_draft(world, campaign_id, stream_id, operation(BRING_BACK, OLDEST))


# --- flows that may not be edited --------------------------------------------------------


async def test_a_redirect_flow_rotates_no_offers_and_refuses_the_edit(world: FakeWorld) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    with pytest.raises(StreamDoesNotRotateOffersError, match="redirect"):
        await given_staged_draft(world, campaign_id, REDIRECT_FLOW, operation(ADD, ADDED))


async def test_another_campaign_s_flow_is_not_found(world: FakeWorld) -> None:
    _, stream_id = await given_mirrored_campaign(world)

    with pytest.raises(StreamNotFoundError):
        await given_staged_draft(world, CampaignId(uuid4()), stream_id, operation(ADD, ADDED))


async def test_a_draft_in_flight_to_the_tracker_is_nobody_s_to_edit(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    view = await locked_view(world.uow, campaign_id, stream_id)
    assert view.draft is not None
    async with world.uow.begin() as transaction:
        await transaction.drafts.change_status(
            view.draft.id, was=DraftStatus.OPEN, becomes=DraftStatus.PUSHING
        )

    with pytest.raises(DraftBeingPushedError):
        await given_staged_draft(world, campaign_id, stream_id, operation(REMOVE, NEWEST))


# --- what the answer is ------------------------------------------------------------------


async def test_the_answer_is_the_flow_a_reload_would_fetch(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    answered = await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))

    reloaded = (await GetEditorView(uow=world.uow)(campaign_id)).streams[1]
    assert answered == reloaded


async def test_one_transaction_and_never_a_word_to_the_tracker(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    world.uow.blocks = 0
    world.admin.calls.clear()

    await EditDraft(uow=world.uow)(
        campaign_id=campaign_id, stream_id=stream_id, operations=(operation(ADD, ADDED),)
    )

    assert world.uow.blocks == 1
    assert world.admin.calls == [], "staging an edit is ours alone until PUSH is pressed"


# --- CANCEL ------------------------------------------------------------------------------


async def test_cancelling_puts_the_tracker_s_own_shares_back_unnormalised(
    world: FakeWorld,
) -> None:
    """The strongest thing CANCEL has to promise: the recalculation goes with the edit.

    This flow summed to 50 before anything was touched. The first edit divided the whole and
    made it sum to 100; cancelling has to undo that too, or the screen would be showing
    numbers this service invented and nobody asked for.
    """
    campaign_id, stream_id = await given_mirrored_campaign(world)
    edited = await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    assert on_screen(edited) == [(ADDED, 34, False), (OLDEST, 33, False), (NEWEST, 33, False)]

    flow = await DiscardDraft(uow=world.uow)(campaign_id=campaign_id, stream_id=stream_id)

    assert on_screen(flow) == [(OLDEST, 25, False), (NEWEST, 25, False)]
    assert (flow.dirty, flow.can_push, flow.diff) == (False, False, None)


async def test_a_pin_survives_the_cancel(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await SetOfferPin(uow=world.uow)(
        campaign_id=campaign_id, stream_id=stream_id, offer_id=NEWEST, share=Share(25)
    )
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))

    flow = await DiscardDraft(uow=world.uow)(campaign_id=campaign_id, stream_id=stream_id)

    assert {int(row.offer_id): row.pinned_share for row in flow.rows} == {OLDEST: None, NEWEST: 25}


async def test_cancelling_a_flow_with_nothing_staged_says_nothing(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    flow = await DiscardDraft(uow=world.uow)(campaign_id=campaign_id, stream_id=stream_id)

    assert not flow.dirty
    assert on_screen(flow) == [(OLDEST, 25, False), (NEWEST, 25, False)]


async def test_the_next_edit_after_a_cancel_opens_a_draft_of_its_own(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    discarded = await locked_view(world.uow, campaign_id, stream_id)
    assert discarded.draft is not None
    first = discarded.draft.id
    await DiscardDraft(uow=world.uow)(campaign_id=campaign_id, stream_id=stream_id)

    await given_staged_draft(world, campaign_id, stream_id, operation(REMOVE, OLDEST))

    reopened = await locked_view(world.uow, campaign_id, stream_id)
    assert reopened.draft is not None
    assert reopened.draft.id != first, "the discarded one is closed, not reused"


async def test_a_draft_in_flight_is_not_cancellable(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    view = await locked_view(world.uow, campaign_id, stream_id)
    assert view.draft is not None
    async with world.uow.begin() as transaction:
        await transaction.drafts.change_status(
            view.draft.id, was=DraftStatus.OPEN, becomes=DraftStatus.PUSHING
        )

    with pytest.raises(DraftBeingPushedError):
        await DiscardDraft(uow=world.uow)(campaign_id=campaign_id, stream_id=stream_id)


async def test_cancelling_another_campaign_s_flow_is_not_found(world: FakeWorld) -> None:
    _, stream_id = await given_mirrored_campaign(world)

    with pytest.raises(StreamNotFoundError):
        await DiscardDraft(uow=world.uow)(campaign_id=CampaignId(uuid4()), stream_id=stream_id)


async def test_cancelling_a_redirect_flow_is_refused(world: FakeWorld) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    with pytest.raises(StreamDoesNotRotateOffersError):
        await DiscardDraft(uow=world.uow)(campaign_id=campaign_id, stream_id=REDIRECT_FLOW)


async def test_cancelling_costs_one_transaction_and_no_tracker_call(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    world.uow.blocks = 0
    world.admin.calls.clear()

    await DiscardDraft(uow=world.uow)(campaign_id=campaign_id, stream_id=stream_id)

    assert world.uow.blocks == 1
    assert world.admin.calls == []
