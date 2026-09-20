"""Share redistribution for the offers of one Keitaro stream — the arithmetic of the task.

The rule, in the order it applies:

1. a pinned row keeps the share it was pinned at;
2. a removed row gets 0 and takes no part in the division;
3. what is left of 100 is split between the free active rows with `divmod`;
4. the remainder is handed out one unit at a time, most recently activated row first —
   the last add, the last BRING BACK, and for rows that arrived from the tracker, the most
   recent `created_at`.

Whole numbers throughout: `round()` and floats are what put 99% on the screen. Point 4 is
the one that was not obvious. `seq ASC` is the rule that suggests itself, and it disagrees
with the reference tool in two of the four states taken from the video; those four states
are the fixtures in `tests/domain/test_shares.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from adrobot.domain.errors import (
    DuplicateOfferRowError,
    OfferNotInStreamError,
    PinnedSharesExceedTotalError,
)
from adrobot.domain.ids import OfferId
from adrobot.domain.values import TOTAL_SHARE

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True, kw_only=True)
class OfferRow:
    """One offer inside one stream, as the arithmetic sees it.

    `activated_at` is an ordinal and not a clock: whoever builds these rows numbers them by
    when they were last activated, so this module needs no clock and no timezone. `seq` is
    the insertion order and breaks a tie between two rows activated at the same moment.
    `pinned_share` is the pin — `None` means the row takes part in the division.
    """

    offer_id: OfferId
    seq: int
    activated_at: int
    share: int = 0
    pinned_share: int | None = None
    removed: bool = False


def redistribute(rows: tuple[OfferRow, ...]) -> tuple[OfferRow, ...]:
    """Recompute every share in one stream, preserving the caller's row order.

    Raises `PinnedSharesExceedTotalError` when the pins reserve more than the whole, which
    is a state the pin endpoint refuses to create rather than one to resolve here.
    """
    reject_duplicate_rows(rows)
    active = [row for row in rows if not row.removed]

    held: dict[OfferId, int] = {}
    for row in active:
        if row.pinned_share is not None:
            held[row.offer_id] = _clamp(row.pinned_share)
    reserved = sum(held.values())
    if reserved > TOTAL_SHARE:
        raise PinnedSharesExceedTotalError(reserved)

    computed = dict(held)
    free = [row for row in active if row.pinned_share is None]
    if free:
        base, extra = divmod(TOTAL_SHARE - reserved, len(free))
        for index, row in enumerate(sorted(free, key=_most_recently_activated_first)):
            computed[row.offer_id] = base + (1 if index < extra else 0)

    return tuple(replace(row, share=0 if row.removed else computed[row.offer_id]) for row in rows)


def display_order(rows: tuple[OfferRow, ...]) -> tuple[OfferRow, ...]:
    """Order rows for the screen: active by descending share, then by insertion, removed last.

    A separate rule from the remainder above, and not to be confused with it: this sorting
    floats whichever row received the remainder to the top, which is why a wrong tie-break
    is invisible here and plain to see in Keitaro.
    """
    return tuple(sorted(rows, key=lambda row: (row.removed, -row.share, row.seq)))


def pin(
    rows: tuple[OfferRow, ...], offer_id: OfferId, at: int | None = None
) -> tuple[OfferRow, ...]:
    """Fix one row's share, at `at` or at whatever the row shows now.

    Deliberately does not recalculate. In the reference tool a pin moves no other row and
    does not dirty the draft; it only decides what happens at the next edit.
    """
    return replace_row(
        rows, offer_id, lambda row: replace(row, pinned_share=row.share if at is None else at)
    )


def unpin(rows: tuple[OfferRow, ...], offer_id: OfferId) -> tuple[OfferRow, ...]:
    """Release one row's share, likewise without recalculating."""
    return replace_row(rows, offer_id, lambda row: replace(row, pinned_share=None))


def replace_row(
    rows: tuple[OfferRow, ...], offer_id: OfferId, change: Callable[[OfferRow], OfferRow]
) -> tuple[OfferRow, ...]:
    """Apply `change` to the one row carrying `offer_id`, keeping the order of the rest."""
    if not any(row.offer_id == offer_id for row in rows):
        raise OfferNotInStreamError(offer_id)
    return tuple(change(row) if row.offer_id == offer_id else row for row in rows)


def _most_recently_activated_first(row: OfferRow) -> tuple[int, int]:
    """Order free rows for the remainder: last activated first, newest row breaking a tie."""
    return (-row.activated_at, -row.seq)


def reject_duplicate_rows(rows: tuple[OfferRow, ...]) -> None:
    """Refuse a second row for the same offer — the tracker's own unique key forbids one."""
    seen: set[OfferId] = set()
    duplicated: set[OfferId] = set()
    for row in rows:
        if row.offer_id in seen:
            duplicated.add(row.offer_id)
        seen.add(row.offer_id)
    if duplicated:
        raise DuplicateOfferRowError(duplicated)


def _clamp(share: int) -> int:
    """Hold a pin inside [0, 100]. The mirror is tolerant, so the number can be anything."""
    return max(0, min(TOTAL_SHARE, share))
