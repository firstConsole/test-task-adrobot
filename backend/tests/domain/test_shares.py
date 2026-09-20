"""The four states the reference tool was recorded in, and what its screen showed in each.

These numbers are the specification of this project: the task asks for buttons that work
and figures that are right, and these are the figures. They were read off the video frame
by frame, together with the timestamp each state is named after.

Every assertion is a dict of offer id to share. A tuple would pass under the wrong
tie-break rule as well — `seq ASC` produces the same three values in states one and three,
paired with the wrong offers — and the pairing is exactly what a reviewer sees when they
open the stream in Keitaro.
"""

from __future__ import annotations

from adrobot.domain.ids import OfferId
from adrobot.domain.shares import display_order, pin, redistribute
from tests.helpers import offer_row, shares

# t=0. Three offers, as the tracker had them; the row order is their creation order.
INITIAL_FETCH = (
    offer_row(3749, seq=1),
    offer_row(3717, seq=2),
    offer_row(11111, seq=3),
)

# t=124. A fourth offer added from the combobox.
AFTER_ADDING_11112 = (*INITIAL_FETCH, offer_row(11112, seq=4))

# t=208. The offer in the middle removed; it stays on screen, greyed out, at 0%.
AFTER_REMOVING_3717 = (
    offer_row(3749, seq=1),
    offer_row(3717, seq=2, removed=True),
    offer_row(11111, seq=3),
    offer_row(11112, seq=4),
)

# t=328. 3717 pinned at 25 and 11111 removed, then 3749 brought back — which makes 3749 the
# most recently activated row in the stream although it is the oldest one.
AFTER_PINNING_3717_AND_BRINGING_BACK_3749 = (
    offer_row(3749, seq=1, activated_at=5),
    offer_row(3717, seq=2, pinned_share=25),
    offer_row(11111, seq=3, removed=True),
    offer_row(11112, seq=4),
)


def test_the_three_offers_the_tracker_was_already_holding() -> None:
    assert shares(redistribute(INITIAL_FETCH)) == {3749: 33, 3717: 33, 11111: 34}


def test_adding_a_fourth_offer_divides_the_whole_evenly() -> None:
    assert shares(redistribute(AFTER_ADDING_11112)) == {3749: 25, 3717: 25, 11111: 25, 11112: 25}


def test_removing_the_offer_in_the_middle_leaves_it_at_zero() -> None:
    assert shares(redistribute(AFTER_REMOVING_3717)) == {3749: 33, 3717: 0, 11111: 33, 11112: 34}


def test_a_pinned_offer_keeps_its_share_and_the_row_brought_back_takes_the_remainder() -> None:
    computed = shares(redistribute(AFTER_PINNING_3717_AND_BRINGING_BACK_3749))
    assert computed == {3749: 38, 3717: 25, 11111: 0, 11112: 37}


def test_pinning_recalculates_nothing() -> None:
    # t=318, the state that looks like a bug: two active rows at 25% each, adding up to 50,
    # and the reference tool shows no PUSH button. Pinning one of them moves neither.
    fetched = (offer_row(3717, seq=1, share=25), offer_row(11112, seq=2, share=25))

    pinned = pin(fetched, OfferId(3717))

    assert shares(pinned) == {3717: 25, 11112: 25}
    assert pinned[0].pinned_share == 25


def test_the_last_state_is_ordered_the_way_the_screen_ordered_it() -> None:
    computed = redistribute(AFTER_PINNING_3717_AND_BRINGING_BACK_3749)

    assert [int(row.offer_id) for row in display_order(computed)] == [3749, 11112, 3717, 11111]
