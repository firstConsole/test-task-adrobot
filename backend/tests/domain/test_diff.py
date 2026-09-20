"""What the editor tells the user, and what the push tells the tracker.

The test that matters most here is the one saying a pin produces an empty diff: that is the
regression guard for the behaviour that looks like a bug, and PUSH lighting up after a pin
is exactly how it would come back.
"""

from __future__ import annotations

from adrobot.domain.diff import DesiredOffer, DraftDiff, ShareChange, snapshot_hash
from adrobot.domain.draft import StreamDraft, to_kernel_rows
from adrobot.domain.ids import OfferId
from adrobot.domain.values import OfferState
from tests.helpers import offer_row

MIRROR = (
    offer_row(3749, seq=1, share=33),
    offer_row(3717, seq=2, share=33),
    offer_row(11111, seq=3, share=34),
)


def test_an_untouched_draft_changes_nothing() -> None:
    diff = DraftDiff.between(MIRROR, MIRROR)

    assert diff.is_empty
    assert diff.desired == (
        DesiredOffer(offer_id=OfferId(3749), share=33, state=OfferState.ACTIVE),
        DesiredOffer(offer_id=OfferId(3717), share=33, state=OfferState.ACTIVE),
        DesiredOffer(offer_id=OfferId(11111), share=34, state=OfferState.ACTIVE),
    )


def test_a_stream_that_does_not_add_up_is_still_not_a_change() -> None:
    # The state at t=318: 25 and 25. Reporting it as dirty would offer to "fix" a stream
    # nobody edited, and the fix would be an unasked-for write to somebody's live campaign.
    half = (offer_row(3717, seq=1, share=25), offer_row(11112, seq=2, share=25))

    assert DraftDiff.between(half, half).is_empty


def test_pinning_a_row_leaves_the_draft_clean() -> None:
    pinned = to_kernel_rows(MIRROR, {OfferId(3717): 33})

    diff = DraftDiff.between(MIRROR, StreamDraft.opened(pinned).rows)

    assert diff.is_empty


def test_adding_an_offer_is_reported_with_the_shares_it_moved() -> None:
    draft = StreamDraft.opened(to_kernel_rows(MIRROR, {})).add(OfferId(11112))

    diff = DraftDiff.between(MIRROR, draft.rows)

    assert diff.added == (OfferId(11112),)
    assert diff.share_changes == (
        ShareChange(offer_id=OfferId(3749), was=33, now=25),
        ShareChange(offer_id=OfferId(3717), was=33, now=25),
        ShareChange(offer_id=OfferId(11111), was=34, now=25),
    )
    assert diff.desired[-1] == DesiredOffer(
        offer_id=OfferId(11112), share=25, state=OfferState.ACTIVE
    )


def test_a_removed_row_is_reported_once_and_pushed_as_disabled() -> None:
    draft = StreamDraft.opened(to_kernel_rows(MIRROR, {})).remove(OfferId(3717))

    diff = DraftDiff.between(MIRROR, draft.rows)

    assert diff.removed == (OfferId(3717),)
    # Reported as removed and not also as a share change: one row, one line on the screen.
    assert [change.offer_id for change in diff.share_changes] == [OfferId(3749), OfferId(11111)]
    # Sent explicitly rather than left out: were the tracker to merge the array instead of
    # replacing it, an omitted row would keep its old share and go on taking traffic.
    assert diff.desired[1] == DesiredOffer(
        offer_id=OfferId(3717), share=0, state=OfferState.DISABLED
    )


def test_a_row_brought_back_is_reported_as_such_and_not_as_an_addition() -> None:
    mirror = (*MIRROR[:1], offer_row(3717, seq=2, share=0, removed=True), *MIRROR[2:])
    draft = StreamDraft.opened(to_kernel_rows(mirror, {})).bring_back(OfferId(3717))

    diff = DraftDiff.between(mirror, draft.rows)

    assert diff.brought_back == (OfferId(3717),)
    assert diff.added == ()


def test_an_offer_added_and_taken_back_again_reaches_neither_the_summary_nor_the_push() -> None:
    draft = StreamDraft.opened(to_kernel_rows(MIRROR, {})).add(OfferId(11112))

    diff = DraftDiff.between(MIRROR, draft.remove(OfferId(11112)).rows)

    assert diff.is_empty
    assert [int(offer.offer_id) for offer in diff.desired] == [3749, 3717, 11111]


def test_the_snapshot_hash_ignores_the_order_the_rows_were_read_in() -> None:
    assert snapshot_hash(MIRROR) == snapshot_hash(tuple(reversed(MIRROR)))


def test_the_snapshot_hash_follows_what_the_tracker_owns_and_nothing_of_ours() -> None:
    ours = to_kernel_rows(MIRROR, {OfferId(3717): 33})
    restamped = tuple(
        offer_row(int(row.offer_id), seq=9, activated_at=9, share=row.share) for row in MIRROR
    )

    assert snapshot_hash(ours) == snapshot_hash(MIRROR)
    assert snapshot_hash(restamped) == snapshot_hash(MIRROR)


def test_the_snapshot_hash_changes_when_the_tracker_does() -> None:
    edited_elsewhere = (*MIRROR[:2], offer_row(11111, seq=3, share=30))
    disabled_elsewhere = (*MIRROR[:2], offer_row(11111, seq=3, share=34, removed=True))

    assert snapshot_hash(edited_elsewhere) != snapshot_hash(MIRROR)
    assert snapshot_hash(disabled_elsewhere) != snapshot_hash(MIRROR)
