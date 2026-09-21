"""That the in-memory database behaves the way the persistence port says a database behaves.

The tracker's fake has `tests/test_fakes.py` for the same reason this exists: a fake nobody
tests is a second source of truth, and the bugs it hides are the kind where every scenario
passes and PostgreSQL does something else. What is asserted here is only what a use case of
stage 7 leans on — the pin joined into both sides of a flow, the one live draft, the
statuses that refuse to move twice, and the catalogue's ranking.

The other half of each promise is held to PostgreSQL in `tests/infrastructure/`, against the
repository these imitate.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from adrobot.application.errors import (
    DraftAlreadyOpenError,
    DraftStatusChangedError,
    PushAttemptSettledError,
    StreamNotFoundError,
)
from adrobot.application.push import PushOutcome
from adrobot.domain.diff import DesiredOffer, snapshot_hash
from adrobot.domain.draft import DraftStatus
from adrobot.domain.ids import CampaignId, KeitaroStreamId, OfferId
from adrobot.domain.offer import Offer
from adrobot.domain.values import OfferState, Share
from tests.fakes import FIRST_MOMENT, REFERENCE_OFFERS
from tests.helpers import given_mirrored_campaign, locked_view, shares

if TYPE_CHECKING:
    from adrobot.domain.shares import OfferRow
    from tests.wiring import FakeWorld

PINNED, FREE = REFERENCE_OFFERS[1], REFERENCE_OFFERS[0]


def _offer(offer_id: int, name: str) -> Offer:
    return Offer(id=OfferId(offer_id), name=name, state="active")


# --- the mirror a flow is read as --------------------------------------------------------


async def test_a_flow_read_back_carries_the_tracker_s_own_shares(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    view = await locked_view(world.uow, campaign_id, stream_id)

    assert shares(view.mirror_rows) == {FREE: 25, PINNED: 25}, "50% is a real clean state"
    assert view.draft is None


async def test_another_campaign_s_flow_is_simply_not_there(world: FakeWorld) -> None:
    _, stream_id = await given_mirrored_campaign(world)

    with pytest.raises(StreamNotFoundError):
        await locked_view(world.uow, CampaignId(uuid4()), stream_id)


# --- pins --------------------------------------------------------------------------------


async def test_a_pin_reaches_the_mirror_rows_and_moves_no_share(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    async with world.uow.begin() as transaction:
        await transaction.pins.hold(
            campaign_id=campaign_id, stream_id=stream_id, offer_id=PINNED, share=Share(40)
        )

    view = await locked_view(world.uow, campaign_id, stream_id)
    held = {int(row.offer_id): row.pinned_share for row in view.mirror_rows}
    assert held == {FREE: None, PINNED: 40}
    assert shares(view.mirror_rows) == {FREE: 25, PINNED: 25}, "pinning recalculates nothing"


async def test_a_pin_is_joined_into_the_draft_rows_as_well(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    view = await locked_view(world.uow, campaign_id, stream_id)

    async with world.uow.begin() as transaction:
        await transaction.drafts.open_for(
            campaign_id=campaign_id,
            stream_id=stream_id,
            rows=view.mirror_rows,
            base_snapshot_hash=snapshot_hash(view.mirror_rows),
        )
        await transaction.pins.hold(
            campaign_id=campaign_id, stream_id=stream_id, offer_id=PINNED, share=Share(40)
        )

    reread = await locked_view(world.uow, campaign_id, stream_id)
    assert reread.draft is not None
    assert {int(row.offer_id): row.pinned_share for row in reread.draft.rows} == {
        FREE: None,
        PINNED: 40,
    }


async def test_releasing_a_pin_nobody_holds_says_nothing(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)

    async with world.uow.begin() as transaction:
        await transaction.pins.release(
            campaign_id=campaign_id, stream_id=stream_id, offer_id=PINNED
        )


async def test_a_pin_is_refused_on_a_flow_that_is_not_this_campaign_s(world: FakeWorld) -> None:
    _, stream_id = await given_mirrored_campaign(world)

    async with world.uow.begin() as transaction:
        with pytest.raises(StreamNotFoundError):
            await transaction.pins.hold(
                campaign_id=CampaignId(uuid4()),
                stream_id=stream_id,
                offer_id=PINNED,
                share=Share(40),
            )


# --- drafts ------------------------------------------------------------------------------


async def test_a_flow_may_hold_one_live_draft_at_a_time(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    view = await locked_view(world.uow, campaign_id, stream_id)

    async with world.uow.begin() as transaction:
        await transaction.drafts.open_for(
            campaign_id=campaign_id,
            stream_id=stream_id,
            rows=view.mirror_rows,
            base_snapshot_hash=snapshot_hash(view.mirror_rows),
        )
        with pytest.raises(DraftAlreadyOpenError):
            await transaction.drafts.open_for(
                campaign_id=campaign_id,
                stream_id=stream_id,
                rows=view.mirror_rows,
                base_snapshot_hash=snapshot_hash(view.mirror_rows),
            )


async def test_a_discarded_draft_stops_being_the_live_one(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    view = await locked_view(world.uow, campaign_id, stream_id)

    async with world.uow.begin() as transaction:
        draft = await transaction.drafts.open_for(
            campaign_id=campaign_id,
            stream_id=stream_id,
            rows=view.mirror_rows,
            base_snapshot_hash=snapshot_hash(view.mirror_rows),
        )
        await transaction.drafts.change_status(
            draft.id, was=DraftStatus.OPEN, becomes=DraftStatus.DISCARDED
        )

    reread = await locked_view(world.uow, campaign_id, stream_id)
    assert reread.draft is None, "closed softly, and gone from the screen all the same"


async def test_a_draft_that_moved_first_refuses_the_second_mover(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    view = await locked_view(world.uow, campaign_id, stream_id)

    async with world.uow.begin() as transaction:
        draft = await transaction.drafts.open_for(
            campaign_id=campaign_id,
            stream_id=stream_id,
            rows=view.mirror_rows,
            base_snapshot_hash=snapshot_hash(view.mirror_rows),
        )
        await transaction.drafts.change_status(
            draft.id, was=DraftStatus.OPEN, becomes=DraftStatus.PUSHING
        )
        with pytest.raises(DraftStatusChangedError, match="pushing"):
            await transaction.drafts.change_status(
                draft.id, was=DraftStatus.OPEN, becomes=DraftStatus.PUSHING
            )


async def test_the_rows_of_a_draft_are_replaced_whole_and_keep_their_ordinals(
    world: FakeWorld,
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    view = await locked_view(world.uow, campaign_id, stream_id)
    rearranged = tuple(replace(row, share=50) for row in reversed(view.mirror_rows))

    async with world.uow.begin() as transaction:
        draft = await transaction.drafts.open_for(
            campaign_id=campaign_id,
            stream_id=stream_id,
            rows=view.mirror_rows,
            base_snapshot_hash=snapshot_hash(view.mirror_rows),
        )
        await transaction.drafts.replace_rows(draft.id, rearranged)

    reread = await locked_view(world.uow, campaign_id, stream_id)
    assert reread.draft is not None
    assert shares(reread.draft.rows) == {FREE: 50, PINNED: 50}
    assert [row.seq for row in reread.draft.rows] == [1, 2], "read back in ordinal order"


# --- push attempts -----------------------------------------------------------------------


async def test_an_attempt_is_opened_in_flight_and_closed_once(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    view = await locked_view(world.uow, campaign_id, stream_id)
    desired = (DesiredOffer(offer_id=FREE, share=100, state=OfferState.ACTIVE),)

    async with world.uow.begin() as transaction:
        draft = await transaction.drafts.open_for(
            campaign_id=campaign_id,
            stream_id=stream_id,
            rows=view.mirror_rows,
            base_snapshot_hash=snapshot_hash(view.mirror_rows),
        )
        attempt = await transaction.pushes.start(
            draft_id=draft.id, desired=desired, correlation_id="cid-1"
        )
        in_flight = await transaction.pushes.latest_for(draft.id)
        assert in_flight is not None
        assert in_flight.outcome is PushOutcome.IN_FLIGHT
        assert in_flight.correlation_id == "cid-1"

        await transaction.pushes.settle(attempt, PushOutcome.APPLIED)
        with pytest.raises(PushAttemptSettledError):
            await transaction.pushes.settle(attempt, PushOutcome.FAILED)

        settled = await transaction.pushes.latest_for(draft.id)
        assert settled is not None
        assert settled.outcome is PushOutcome.APPLIED


# --- the offer catalogue -----------------------------------------------------------------


async def test_the_id_prefix_outranks_a_name_that_merely_contains_it(world: FakeWorld) -> None:
    async with world.uow.begin() as transaction:
        await transaction.offers.upsert_catalogue(
            (_offer(11104, "Oxys"), _offer(11999, "11104 Special")), at=FIRST_MOMENT
        )
        found = await transaction.offers.search("11104", limit=10)

    assert [offer.id for offer in found] == [11104, 11999]


async def test_an_offer_the_tracker_dropped_leaves_the_search_but_still_labels_a_row(
    world: FakeWorld,
) -> None:
    async with world.uow.begin() as transaction:
        await transaction.offers.upsert_catalogue(
            (_offer(11104, "Oxys"), _offer(11999, "Miaflow")), at=FIRST_MOMENT
        )
        await transaction.offers.upsert_catalogue((_offer(11104, "Oxys"),), at=FIRST_MOMENT)

        assert [offer.id for offer in await transaction.offers.search(None, limit=10)] == [11104]
        labelled = await transaction.offers.by_ids([OfferId(11999)])

    assert labelled[OfferId(11999)].name == "Miaflow", "a flow row keeps its label"


# --- the block -----------------------------------------------------------------------------


async def _edit_then_fail(
    world: FakeWorld,
    campaign_id: CampaignId,
    stream_id: KeitaroStreamId,
    rows: tuple[OfferRow, ...],
) -> None:
    """Open a draft, hold a pin, and then have the request fail with both writes unflushed."""
    async with world.uow.begin() as transaction:
        await transaction.drafts.open_for(
            campaign_id=campaign_id,
            stream_id=stream_id,
            rows=rows,
            base_snapshot_hash=snapshot_hash(rows),
        )
        await transaction.pins.hold(
            campaign_id=campaign_id, stream_id=stream_id, offer_id=PINNED, share=Share(40)
        )
        failed = "the request failed after both writes"
        raise RuntimeError(failed)


async def test_raising_out_of_a_block_loses_its_pins_and_its_drafts(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    view = await locked_view(world.uow, campaign_id, stream_id)

    with pytest.raises(RuntimeError):
        await _edit_then_fail(world, campaign_id, stream_id, view.mirror_rows)

    reread = await locked_view(world.uow, campaign_id, stream_id)
    assert reread.draft is None
    assert all(row.pinned_share is None for row in reread.mirror_rows)
