"""The draft aggregate, walked the way the reference tool was walked in the video.

`test_shares.py` asserts the four states as data. This file reaches them through the
operations a user actually performs, which is the other half of the same claim: the rule is
right, and the buttons that invoke it are wired to it correctly.
"""

from __future__ import annotations

import pytest

from adrobot.domain.draft import (
    DraftOperation,
    DraftOperationKind,
    StreamDraft,
    to_kernel_rows,
)
from adrobot.domain.errors import (
    DuplicateOfferRowError,
    OfferAlreadyInStreamError,
    OfferAlreadyRemovedError,
    OfferNotInStreamError,
    OfferNotRemovedError,
)
from adrobot.domain.ids import OfferId
from adrobot.domain.shares import display_order
from tests.helpers import offer_row, shares

# The stream as the tracker held it at t=0, carrying the tracker's own numbers: a draft is
# opened on what was read, not on what we would have computed.
FETCHED = (
    offer_row(3749, seq=1, share=33),
    offer_row(3717, seq=2, share=33),
    offer_row(11111, seq=3, share=34),
)


def opened(pins: dict[OfferId, int] | None = None) -> StreamDraft:
    return StreamDraft.opened(to_kernel_rows(FETCHED, pins or {}))


def test_opening_a_draft_leaves_a_stream_that_does_not_add_up_as_it_found_it() -> None:
    # t=318 in the video: two active rows at 25% each and no PUSH button. Normalising this
    # would invent data and would light PUSH up on a stream nobody has touched.
    half = (offer_row(3717, seq=1, share=25), offer_row(11112, seq=2, share=25))

    assert shares(StreamDraft.opened(half).rows) == {3717: 25, 11112: 25}


def test_the_video_sequence_ends_on_the_frame_the_video_ends_on() -> None:
    draft = opened()

    draft = draft.add(OfferId(11112))
    assert shares(draft.rows) == {3749: 25, 3717: 25, 11111: 25, 11112: 25}

    draft = draft.remove(OfferId(3717))
    assert shares(draft.rows) == {3749: 33, 3717: 0, 11111: 33, 11112: 34}

    draft = draft.remove(OfferId(11111)).remove(OfferId(3749))
    assert shares(draft.rows) == {3749: 0, 3717: 0, 11111: 0, 11112: 100}

    # The pin is written to the mirror between two edits, so the next request reopens the
    # draft with it — which is the whole reason a pin does not live inside the aggregate.
    draft = StreamDraft.opened(to_kernel_rows(draft.rows, {OfferId(3717): 25}))
    draft = draft.bring_back(OfferId(3717)).bring_back(OfferId(3749))

    assert shares(draft.rows) == {3749: 38, 3717: 25, 11111: 0, 11112: 37}
    assert [row.share for row in display_order(draft.rows)] == [38, 37, 25, 0]


def test_a_row_brought_back_takes_the_remainder_ahead_of_rows_that_never_left() -> None:
    draft = opened().remove(OfferId(3749)).bring_back(OfferId(3749))

    assert shares(draft.rows) == {3749: 34, 3717: 33, 11111: 33}


def test_a_pinned_row_keeps_its_share_while_the_rest_is_divided() -> None:
    draft = opened({OfferId(3717): 25})

    assert shares(draft.remove(OfferId(11111)).rows) == {3749: 75, 3717: 25, 11111: 0}


def test_reopening_a_draft_with_a_new_pin_moves_nothing_and_journals_nothing() -> None:
    rows = (offer_row(3717, seq=1, share=25), offer_row(11112, seq=2, share=25))

    pinned = StreamDraft.opened(to_kernel_rows(rows, {OfferId(3717): 25}))

    assert shares(pinned.rows) == {3717: 25, 11112: 25}
    assert pinned.journal == ()


def test_the_pin_mapping_decides_which_rows_are_pinned_and_the_rows_do_not() -> None:
    carrying_a_stale_pin = (offer_row(3749, seq=1, pinned_share=90), offer_row(3717, seq=2))

    joined = to_kernel_rows(carrying_a_stale_pin, {OfferId(3717): 25})

    assert [row.pinned_share for row in joined] == [None, 25]


def test_the_journal_records_what_was_applied_in_order() -> None:
    draft = opened().add(OfferId(11112)).remove(OfferId(3749))

    assert draft.journal == (
        DraftOperation(kind=DraftOperationKind.ADD, offer_id=OfferId(11112)),
        DraftOperation(kind=DraftOperationKind.REMOVE, offer_id=OfferId(3749)),
    )


def test_an_operation_leaves_the_aggregate_it_was_called_on_alone() -> None:
    draft = opened()

    draft.add(OfferId(11112))

    assert shares(draft.rows) == {3749: 33, 3717: 33, 11111: 34}
    assert draft.journal == ()


def test_the_first_offer_of_an_empty_stream_takes_the_whole() -> None:
    assert shares(StreamDraft.opened(()).add(OfferId(3749)).rows) == {3749: 100}


def test_removing_every_row_is_allowed_and_leaves_the_stream_at_zero() -> None:
    draft = opened()

    for offer_id in (3749, 3717, 11111):
        draft = draft.remove(OfferId(offer_id))

    assert shares(draft.rows) == {3749: 0, 3717: 0, 11111: 0}


def test_adding_an_offer_the_stream_already_carries_is_refused() -> None:
    with pytest.raises(OfferAlreadyInStreamError):
        opened().add(OfferId(3749))

    # And a removed row still counts as carried: it comes back through bring_back.
    with pytest.raises(OfferAlreadyInStreamError):
        opened().remove(OfferId(3749)).add(OfferId(3749))


def test_removing_a_row_that_is_already_out_is_refused() -> None:
    with pytest.raises(OfferAlreadyRemovedError):
        opened().remove(OfferId(3749)).remove(OfferId(3749))


def test_bringing_back_a_row_that_never_left_is_refused() -> None:
    with pytest.raises(OfferNotRemovedError):
        opened().bring_back(OfferId(3749))


def test_an_operation_on_an_offer_the_stream_does_not_carry_is_refused() -> None:
    with pytest.raises(OfferNotInStreamError):
        opened().remove(OfferId(99999))

    with pytest.raises(OfferNotInStreamError):
        opened().bring_back(OfferId(99999))


def test_a_draft_cannot_be_opened_on_two_rows_for_the_same_offer() -> None:
    with pytest.raises(DuplicateOfferRowError):
        StreamDraft.opened((offer_row(3749, seq=1), offer_row(3749, seq=2)))
