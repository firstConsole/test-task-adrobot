"""PUSH TO KT, against both semantics the tracker's update might have.

The two promises worth the file are at the top: the transaction is never open across the
tracker call, and a push that fails hands the edits back instead of losing them. Everything
else — what the payload says, what the mirror looks like afterwards — follows from those
two being got right.

`FakeKeitaroAdmin(merges=...)` is parametrised wherever the answer must not depend on it.
Whether `PUT /streams/{id}` replaces the offers array or merges into it is the open question
of `docs/keitaro-api-notes.md`, and the push is meant to be correct in both worlds.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from adrobot.application.errors import (
    DraftBeingPushedError,
    NothingToPushError,
    PushBlockedError,
    StreamDoesNotRotateOffersError,
    StreamNotFoundError,
    UpstreamProtocolError,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from adrobot.application.push import PushOutcome
from adrobot.application.use_cases.editor import GetEditorView
from adrobot.application.use_cases.pin_offer import SetOfferPin
from adrobot.application.use_cases.push_draft import WEDGED_AFTER, PushDraft
from adrobot.domain.draft import DraftOperationKind, DraftStatus
from adrobot.domain.ids import CampaignId, KeitaroStreamId, OfferId
from adrobot.domain.values import Share
from tests.fake_persistence import FakeUnitOfWork, WatchesTheDatabase
from tests.fakes import FIRST_STREAM_ID, REFERENCE_OFFERS, given_reference_campaign
from tests.helpers import given_mirrored_campaign, given_staged_draft, locked_view, operation

if TYPE_CHECKING:
    from adrobot.application.dto import StreamEditorView
    from tests.wiring import FakeWorld

OLDEST, NEWEST = REFERENCE_OFFERS
ADDED = OfferId(11111)
REDIRECT_FLOW = KeitaroStreamId(FIRST_STREAM_ID)
ADD, REMOVE = DraftOperationKind.ADD, DraftOperationKind.REMOVE

BOTH_SEMANTICS = pytest.mark.parametrize(
    "merges", [False, True], ids=["tracker-replaces-the-array", "tracker-merges-the-array"]
)


def pushing(world: FakeWorld) -> PushDraft:
    return PushDraft(
        admin=world.admin, uow=world.uow, clock=world.clock, correlation=world.correlation
    )


def on_screen(flow: StreamEditorView) -> list[tuple[int, int, bool]]:
    return [(int(row.offer_id), row.share, row.removed) for row in flow.rows]


def held_by_the_tracker(world: FakeWorld, stream_id: KeitaroStreamId) -> dict[int, tuple[int, str]]:
    return {
        int(row.offer_id): (row.share, row.state) for row in world.admin.streams[stream_id].offers
    }


# --- the shape of the thing --------------------------------------------------------------


async def test_the_database_is_never_held_open_across_the_tracker_call(world: FakeWorld) -> None:
    """The one rule this scenario exists to obey, as a test rather than as a paragraph.

    `WatchesTheDatabase` fails on any port call made while a transaction is open, so a push
    written as `async with uow.begin(): await admin...` cannot pass — which is the version
    that empties the connection pool the first time Keitaro is slow. The watcher holds a
    unit of work of its own over the same tables, because what it has to see is the block
    the scenario opens, not the one a fixture opened earlier.
    """
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(REMOVE, OLDEST))
    watched = FakeUnitOfWork(world.clock, world.uow.tables)
    tracker = WatchesTheDatabase(watched)
    given_reference_campaign(tracker)

    await PushDraft(admin=tracker, uow=watched, clock=world.clock, correlation=world.correlation)(
        campaign_id=campaign_id, stream_id=stream_id
    )

    assert not watched.open
    assert watched.blocks == 2, "one transaction each side of the call, never one around it"


@BOTH_SEMANTICS
async def test_a_removed_row_travels_explicitly_and_survives_on_screen(
    world: FakeWorld,
    merges: bool,  # noqa: FBT001  # a parametrised semantics, not a flag
) -> None:
    """The behaviour the reference tool is recognised by, under either update semantics.

    A tracker that replaces the array drops the row; one that merges keeps it switched off.
    Either way it is still drawn — grey, at 0%, with BRING BACK live — because the mirror
    write tombstones what the answer no longer carries instead of deleting it.
    """
    world.admin.merges = merges
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(REMOVE, OLDEST))

    flow = await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)

    assert on_screen(flow) == [(NEWEST, 100, False), (OLDEST, 0, True)]
    assert not flow.dirty, "the draft is closed, so the flow reads from the mirror again"
    assert held_by_the_tracker(world, stream_id)[NEWEST] == (100, "active")


@BOTH_SEMANTICS
async def test_the_tracker_is_left_holding_exactly_what_the_screen_showed(
    world: FakeWorld,
    merges: bool,  # noqa: FBT001  # a parametrised semantics, not a flag
) -> None:
    world.admin.merges = merges
    campaign_id, stream_id = await given_mirrored_campaign(world)
    staged = await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    assert on_screen(staged) == [(ADDED, 34, False), (OLDEST, 33, False), (NEWEST, 33, False)]

    await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)

    assert held_by_the_tracker(world, stream_id) == {
        OLDEST: (33, "active"),
        NEWEST: (33, "active"),
        ADDED: (34, "active"),
    }


async def test_the_attempt_is_recorded_against_the_request_that_made_it(
    world: FakeWorld,
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    view = await locked_view(world.uow, campaign_id, stream_id)
    assert view.draft is not None

    await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)

    async with world.uow.begin() as transaction:
        attempt = await transaction.pushes.latest_for(view.draft.id)
    assert attempt is not None
    assert attempt.outcome is PushOutcome.APPLIED
    assert attempt.correlation_id == world.correlation.issued[-1]


async def test_a_pin_survives_the_push(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await SetOfferPin(uow=world.uow)(
        campaign_id=campaign_id, stream_id=stream_id, offer_id=NEWEST, share=Share(25)
    )
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))

    flow = await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)

    assert {int(row.offer_id): row.pinned_share for row in flow.rows}[NEWEST] == 25


# --- when the tracker will not play ------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "outcome"),
    [
        (UpstreamUnavailableError("read timed out"), PushOutcome.INDETERMINATE),
        (UpstreamUnavailableError("bad gateway", status=502), PushOutcome.FAILED),
        (UpstreamRejectedError("no such offer", status=406), PushOutcome.FAILED),
        (UpstreamProtocolError("flow did not take the push"), PushOutcome.MISMATCHED),
    ],
    ids=["no-answer", "answered-badly", "refused", "wrote-something-else"],
)
async def test_a_failed_push_hands_the_draft_back_with_its_rows_untouched(
    world: FakeWorld, failure: Exception, outcome: PushOutcome
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    staged = await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    view = await locked_view(world.uow, campaign_id, stream_id)
    assert view.draft is not None
    world.admin.fail_on("replace_stream_offers", failure)

    with pytest.raises(type(failure)):
        await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)

    async with world.uow.begin() as transaction:
        attempt = await transaction.pushes.latest_for(view.draft.id)
    assert attempt is not None
    assert attempt.outcome is outcome
    after = (await GetEditorView(uow=world.uow)(campaign_id)).streams[1]
    assert after.dirty, "a bad minute on the network is not a lost edit"
    assert after.can_push
    assert on_screen(after) == on_screen(staged)


# --- what the push refuses ---------------------------------------------------------------


async def test_pushing_a_flow_with_no_draft_is_refused(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    world.admin.calls.clear()

    with pytest.raises(NothingToPushError):
        await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)

    assert world.admin.calls == []


async def test_a_draft_that_would_leave_no_active_offer_is_refused_in_those_words(
    world: FakeWorld,
) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(
        world, campaign_id, stream_id, operation(REMOVE, OLDEST), operation(REMOVE, NEWEST)
    )
    world.admin.calls.clear()

    with pytest.raises(PushBlockedError, match="no active offer left"):
        await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)

    assert world.admin.calls == [], "refused before the tracker was touched"


async def test_a_redirect_flow_has_nothing_to_push(world: FakeWorld) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    with pytest.raises(StreamDoesNotRotateOffersError):
        await pushing(world)(campaign_id=campaign_id, stream_id=REDIRECT_FLOW)


async def test_a_flow_this_service_has_never_mirrored_is_not_found(world: FakeWorld) -> None:
    campaign_id, _ = await given_mirrored_campaign(world)

    with pytest.raises(StreamNotFoundError):
        await pushing(world)(campaign_id=campaign_id, stream_id=KeitaroStreamId(999_999))


async def test_another_campaign_s_flow_is_not_found(world: FakeWorld) -> None:
    _, stream_id = await given_mirrored_campaign(world)

    with pytest.raises(StreamNotFoundError):
        await pushing(world)(campaign_id=CampaignId(uuid4()), stream_id=stream_id)


async def test_pushing_twice_finds_nothing_to_push_the_second_time(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)

    with pytest.raises(NothingToPushError):
        await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)


# --- a push that never came back ---------------------------------------------------------


async def test_a_push_still_running_is_not_taken_over(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    world.admin.fail_on("replace_stream_offers", TimeoutError("the process died here"))
    with pytest.raises(TimeoutError):
        await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)

    with pytest.raises(DraftBeingPushedError):
        await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)


async def test_a_push_wedged_past_the_deadline_is_taken_over(world: FakeWorld) -> None:
    """Without this a flow whose process died is `pushing` for ever: nobody can edit it,
    push it or cancel it, and the only way out is SQL by hand."""
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    view = await locked_view(world.uow, campaign_id, stream_id)
    assert view.draft is not None
    world.admin.fail_on("replace_stream_offers", TimeoutError("the process died here"))
    with pytest.raises(TimeoutError):
        await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)
    world.admin.failures.clear()
    world.clock.advance(WEDGED_AFTER + timedelta(seconds=1))

    flow = await pushing(world)(campaign_id=campaign_id, stream_id=stream_id)

    assert not flow.dirty
    assert held_by_the_tracker(world, stream_id)[ADDED] == (34, "active")
    async with world.uow.begin() as transaction:
        abandoned = [
            row
            for row in world.uow.tables.pushes.values()
            if row.draft_id == view.draft.id and row.outcome is PushOutcome.INDETERMINATE
        ]
        assert await transaction.pushes.latest_for(view.draft.id) is not None
    assert len(abandoned) == 1, "the push nobody heard back from is recorded as indeterminate"


async def test_a_draft_left_pushing_is_not_editable_meanwhile(world: FakeWorld) -> None:
    campaign_id, stream_id = await given_mirrored_campaign(world)
    await given_staged_draft(world, campaign_id, stream_id, operation(ADD, ADDED))
    view = await locked_view(world.uow, campaign_id, stream_id)
    assert view.draft is not None
    async with world.uow.begin() as transaction:
        await transaction.drafts.change_status(
            view.draft.id, was=DraftStatus.OPEN, becomes=DraftStatus.PUSHING
        )

    flow = (await GetEditorView(uow=world.uow)(campaign_id)).streams[1]
    assert not flow.can_push
    assert flow.block_reason == "this flow is being pushed right now — wait for that to finish"
