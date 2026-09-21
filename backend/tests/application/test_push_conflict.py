"""Pushing onto a flow somebody edited in Keitaro meanwhile.

The check is against **the tracker** and not against our own mirror, and the difference
matters: the mirror moves whenever somebody presses FETCH STREAMS FROM KT, which is our own
cache catching up and no reason to refuse anything. The tracker moving is another person's
work, and pushing over it without saying so is the failure this file is about.

It rests on two fingerprints of one flow agreeing — one taken of the rows read out of our
tables, one of the rows read off the wire. The last test here is the one that holds them
together; without it a conflict could be reported on every push, for ever, and the only
symptom would be a button that stopped working.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from adrobot.application.errors import DraftConflictError
from adrobot.application.push import PushOutcome
from adrobot.application.use_cases.editor import GetEditorView
from adrobot.application.use_cases.mirror_campaign import SyncCampaign
from adrobot.application.use_cases.push_draft import PushDraft
from adrobot.domain.diff import snapshot_hash
from adrobot.domain.draft import DraftOperationKind
from adrobot.domain.ids import KeitaroStreamId, OfferId
from adrobot.domain.stream import StreamOffer, offer_rows
from tests.fakes import REFERENCE_OFFERS
from tests.helpers import given_mirrored_campaign, given_staged_draft, locked_view, operation

if TYPE_CHECKING:
    from tests.wiring import FakeWorld

OLDEST, NEWEST = REFERENCE_OFFERS
ADDED = OfferId(11111)
ADD, REMOVE = DraftOperationKind.ADD, DraftOperationKind.REMOVE


def pushing(world: FakeWorld) -> PushDraft:
    return PushDraft(
        admin=world.admin, uow=world.uow, clock=world.clock, correlation=world.correlation
    )


def in_the_tracker(world: FakeWorld, stream_id: KeitaroStreamId) -> tuple[StreamOffer, ...]:
    return world.admin.streams[stream_id].offers


def edited_in_keitaro(
    world: FakeWorld, stream_id: KeitaroStreamId, offers: tuple[StreamOffer, ...]
) -> None:
    """Somebody opened the tracker and changed this flow while the draft was being written."""
    world.admin.given_stream(replace(world.admin.streams[stream_id], offers=offers))


async def test_a_flow_edited_in_keitaro_stops_the_push_and_shows_both_states(
    world: FakeWorld,
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    held, other = in_the_tracker(world, stream_id)
    edited_in_keitaro(world, stream_id, (replace(held, share=70), replace(other, share=30)))

    with pytest.raises(DraftConflictError) as refused:
        await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)

    conflict = refused.value
    assert {int(row.offer_id): row.share for row in conflict.held} == {OLDEST: 70, NEWEST: 30}
    assert {int(row.offer_id): row.share for row in conflict.wanted} == {
        OLDEST: 33,
        NEWEST: 33,
        ADDED: 34,
    }


async def test_a_conflict_hands_the_draft_back_and_writes_nothing(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    staged = await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    view = await locked_view(world.uow, campaign_id, stream_id)
    assert view.draft is not None
    held, other = in_the_tracker(world, stream_id)
    edited_in_keitaro(world, stream_id, (replace(held, share=70), replace(other, share=30)))

    with pytest.raises(DraftConflictError):
        await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)

    assert "update_stream" not in world.admin.calls
    assert {int(row.offer_id): row.share for row in in_the_tracker(world, stream_id)} == {
        OLDEST: 70,
        NEWEST: 30,
    }, "the other person's work is exactly as they left it"
    async with world.uow.begin() as transaction:
        attempt = await transaction.pushes.latest_for(view.draft.id)
    assert attempt is not None
    assert attempt.outcome is PushOutcome.CONFLICT, "nothing went wrong, so this is not a failure"
    after = (await GetEditorView(uow=world.uow)(campaign_id)).streams[1]
    assert after.can_push, "the edits are still there and the button is still live"
    assert [row.share for row in after.rows] == [row.share for row in staged.rows]


async def test_overwriting_pushes_over_the_other_person_s_work_on_purpose(
    world: FakeWorld,
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    held, other = in_the_tracker(world, stream_id)
    edited_in_keitaro(world, stream_id, (replace(held, share=70), replace(other, share=30)))

    flow = await pushing(world)(campaign_id=campaign_id, stream_id=stream_id, overwrite=True)

    assert not flow.dirty
    assert {int(row.offer_id): row.share for row in in_the_tracker(world, stream_id)} == {
        OLDEST: 33,
        NEWEST: 33,
        ADDED: 34,
    }


async def test_overwriting_does_not_bother_reading_the_flow_first(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    world.admin.calls.clear()

    await pushing(world)(campaign_id=campaign_id, stream_id=stream_id, overwrite=True)
    overwriting = list(world.admin.calls)

    await given_staged_draft(world, campaign_id, stream_id, operation(REMOVE, NEWEST))
    world.admin.calls.clear()
    await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)

    assert world.admin.calls.count("get_stream") == overwriting.count("get_stream") + 1


async def test_our_own_mirror_moving_is_a_warning_and_not_a_conflict(world: FakeWorld) -> None:
    """FETCH STREAMS FROM KT under a live draft: our cache caught up, nobody edited anything.

    The draft was diffed against a state the tracker still holds, so the push is correct and
    goes ahead. The screen says so rather than the button refusing to work.
    """
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    await SyncCampaign(admin=world.admin, uow=world.uow, clock=world.clock)(campaign_id)

    before = (await GetEditorView(uow=world.uow)(campaign_id)).streams[1]
    flow = await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)

    assert before.can_push
    assert not flow.dirty
    assert {int(row.offer_id): row.share for row in in_the_tracker(world, stream_id)} == {
        OLDEST: 33,
        NEWEST: 33,
        ADDED: 34,
    }


async def test_the_two_readings_of_one_flow_fingerprint_the_same(world: FakeWorld) -> None:
    """The load-bearing agreement: our tables and the wire have to hash to the same thing.

    A disabled row is where the two would part company — the tracker keeps a share beside
    one and our mirror keeps a tombstone — so that is the case seeded here. If they ever
    disagree, every push on a flow with such a row reports a conflict that is not there, and
    nothing else looks wrong.
    """
    campaign_id, stream_id = await given_mirrored_campaign(world)
    held, other = in_the_tracker(world, stream_id)
    edited_in_keitaro(world, stream_id, (replace(held, state="disabled", share=25), other))
    await SyncCampaign(admin=world.admin, uow=world.uow, clock=world.clock)(campaign_id)

    view = await locked_view(world.uow, campaign_id, stream_id)

    assert snapshot_hash(view.mirror_rows) == snapshot_hash(
        offer_rows(in_the_tracker(world, stream_id))
    )


async def test_a_row_the_tracker_dropped_does_not_conflict_for_ever(world: FakeWorld) -> None:
    """Our tombstone and their deletion are one absence spelled two ways.

    This is the case that would otherwise wedge a flow permanently: the mirror keeps drawing
    a row Keitaro no longer returns, so a fingerprint that counted it would never match
    again, and every push on that flow would answer 409 with nothing anybody could do about
    it but overwrite.
    """
    campaign_id, stream_id = await given_mirrored_campaign(world)
    held, _ = in_the_tracker(world, stream_id)
    edited_in_keitaro(world, stream_id, (held,))
    await SyncCampaign(admin=world.admin, uow=world.uow, clock=world.clock)(campaign_id)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))

    view = await locked_view(world.uow, campaign_id, stream_id)
    before = in_the_tracker(world, stream_id)

    flow = await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)

    assert len(view.mirror_rows) == 2, "the dropped row is tombstoned, not deleted"
    assert snapshot_hash(view.mirror_rows) == snapshot_hash(offer_rows(before))
    assert not flow.dirty, "the push went through"
