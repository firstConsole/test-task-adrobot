"""The laws the arithmetic obeys for every stream, not only for the four from the video.

The states in `test_shares.py` say what the reference tool did; these say what cannot
happen whatever the input. Together they are what makes the phrase "the figures are right"
checkable: one file pins the rule, the other pins its consequences.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from adrobot.domain.errors import (
    DuplicateOfferRowError,
    OfferNotInStreamError,
    PinnedSharesExceedTotalError,
)
from adrobot.domain.ids import OfferId
from adrobot.domain.shares import pin, redistribute, unpin
from adrobot.domain.values import TOTAL_SHARE
from tests.helpers import offer_row, shares

if TYPE_CHECKING:
    from adrobot.domain.shares import OfferRow

# Deliberately narrow: a small pool of activation stamps makes ties between rows common,
# which is where the tie-break rule is decided, and a free id above the pool is what the
# properties that add a row use.
_OFFER_IDS = st.integers(min_value=1, max_value=99_999)
_UNUSED_OFFER_ID = 100_000
_STAMPS = st.integers(min_value=1, max_value=12)


@st.composite
def streams(draw: st.DrawFn, *, max_rows: int = 6) -> tuple[OfferRow, ...]:
    """Any stream the pin endpoint would allow to exist: its pins fit inside the whole.

    The shares the rows arrive with are drawn freely and never normalised, because that is
    how a stream read from Keitaro reaches us — its own numbers can add up to anything.
    """
    offer_ids = draw(st.lists(_OFFER_IDS, max_size=max_rows, unique=True))
    budget = TOTAL_SHARE
    rows = []
    for seq, offer_id in enumerate(offer_ids, start=1):
        pinned_share = draw(st.one_of(st.none(), st.integers(min_value=0, max_value=budget)))
        if pinned_share is not None:
            budget -= pinned_share
        rows.append(
            offer_row(
                offer_id,
                seq=seq,
                activated_at=draw(_STAMPS),
                share=draw(st.integers(min_value=0, max_value=TOTAL_SHARE)),
                pinned_share=pinned_share,
                removed=draw(st.booleans()),
            )
        )
    return tuple(rows)


@st.composite
def streams_with_something_to_divide(draw: st.DrawFn) -> tuple[OfferRow, ...]:
    """A stream carrying at least one free active row, built rather than filtered for."""
    rows = draw(streams(max_rows=5))
    return (*rows, offer_row(_UNUSED_OFFER_ID, seq=len(rows) + 1, activated_at=draw(_STAMPS)))


@given(streams_with_something_to_divide())
def test_the_active_rows_divide_the_whole_between_them(rows: tuple[OfferRow, ...]) -> None:
    computed = redistribute(rows)

    assert sum(row.share for row in computed if not row.removed) == TOTAL_SHARE


@given(streams())
def test_a_pinned_row_keeps_exactly_what_it_was_pinned_at(rows: tuple[OfferRow, ...]) -> None:
    for row in redistribute(rows):
        if row.pinned_share is not None and not row.removed:
            assert row.share == row.pinned_share


@given(streams())
def test_a_removed_row_is_zero_and_takes_no_part_in_the_division(
    rows: tuple[OfferRow, ...],
) -> None:
    computed = shares(redistribute(rows))
    kept = tuple(row for row in rows if not row.removed)

    assert all(computed[int(row.offer_id)] == 0 for row in rows if row.removed)
    assert shares(redistribute(kept)) == {
        int(row.offer_id): computed[int(row.offer_id)] for row in kept
    }


@given(streams())
def test_recalculating_a_recalculated_stream_changes_nothing(rows: tuple[OfferRow, ...]) -> None:
    once = redistribute(rows)

    assert redistribute(once) == once


@given(streams_with_something_to_divide())
def test_pinning_a_row_where_it_already_sits_moves_nothing(rows: tuple[OfferRow, ...]) -> None:
    # The reference tool's behaviour at t=318, as a law: a pin decides what happens at the
    # next edit and nothing about the present, so even a recalculation would find no work.
    computed = redistribute(rows)

    for row in computed:
        if row.pinned_share is None and not row.removed:
            assert shares(redistribute(pin(computed, row.offer_id))) == shares(computed)


@given(rows=streams(), data=st.data())
def test_the_result_does_not_depend_on_the_order_the_rows_arrive_in(
    rows: tuple[OfferRow, ...], data: st.DataObject
) -> None:
    shuffled = tuple(data.draw(st.permutations(rows)))

    assert shares(redistribute(shuffled)) == shares(redistribute(rows))


@given(streams())
def test_adding_a_row_raises_nobody_else_s_share(rows: tuple[OfferRow, ...]) -> None:
    before = shares(redistribute(rows))
    latest = max((row.activated_at for row in rows), default=0) + 1
    added = offer_row(_UNUSED_OFFER_ID, seq=len(rows) + 1, activated_at=latest)

    after = shares(redistribute((*rows, added)))

    assert all(after[offer_id] <= before[offer_id] for offer_id in before)


@given(streams())
def test_every_share_is_a_whole_number_inside_the_range(rows: tuple[OfferRow, ...]) -> None:
    for row in redistribute(rows):
        assert isinstance(row.share, int)
        assert 0 <= row.share <= TOTAL_SHARE


def test_an_empty_stream_stays_empty() -> None:
    assert redistribute(()) == ()


def test_a_stream_with_nothing_free_keeps_what_its_pins_hold() -> None:
    # 20 + 30, and no attempt to make it 100. There is no invariant saying a stored stream
    # adds up to the whole — only one saying a division does.
    pinned = (offer_row(1, seq=1, pinned_share=20), offer_row(2, seq=2, pinned_share=30))

    assert shares(redistribute(pinned)) == {1: 20, 2: 30}
    assert shares(redistribute((offer_row(1, seq=1, share=50, removed=True),))) == {1: 0}


@given(st.integers(min_value=TOTAL_SHARE + 1, max_value=300))
def test_pins_that_reserve_more_than_the_whole_are_refused(reserved: int) -> None:
    rows = (
        offer_row(1, seq=1, pinned_share=TOTAL_SHARE),
        offer_row(2, seq=2, pinned_share=reserved - TOTAL_SHARE),
    )

    with pytest.raises(PinnedSharesExceedTotalError):
        redistribute(rows)


def test_a_pin_outside_the_range_is_held_inside_it() -> None:
    # Reachable because the mirror of somebody else's data is tolerant: the share a pin
    # captures is whatever the tracker last reported for that row.
    assert shares(redistribute((offer_row(1, seq=1, pinned_share=150), offer_row(2, seq=2)))) == {
        1: 100,
        2: 0,
    }
    assert shares(redistribute((offer_row(1, seq=1, pinned_share=-5), offer_row(2, seq=2)))) == {
        1: 0,
        2: 100,
    }


def test_the_same_offer_twice_in_one_stream_is_refused() -> None:
    with pytest.raises(DuplicateOfferRowError):
        redistribute((offer_row(3749, seq=1), offer_row(3749, seq=2)))


def test_a_pin_at_a_chosen_value_holds_until_it_is_released() -> None:
    rows = redistribute((offer_row(1, seq=1), offer_row(2, seq=2), offer_row(3, seq=3)))

    held = redistribute(pin(rows, OfferId(1), at=50))
    assert shares(held) == {1: 50, 2: 25, 3: 25}

    assert shares(redistribute(unpin(held, OfferId(1)))) == {1: 33, 2: 33, 3: 34}


def test_pinning_an_offer_the_stream_does_not_carry_is_an_error() -> None:
    with pytest.raises(OfferNotInStreamError):
        pin((offer_row(1, seq=1),), OfferId(2))

    with pytest.raises(OfferNotInStreamError):
        unpin((offer_row(1, seq=1),), OfferId(2))
