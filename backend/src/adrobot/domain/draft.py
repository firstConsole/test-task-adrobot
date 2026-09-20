"""The stream editor's draft: the rows being arranged, and what has been done to them.

Every operation returns a new aggregate, already recalculated, so the figures on screen are
never one edit behind the action that produced them.

The pin is not part of this aggregate. It lives in the mirror, because it has to survive
both a push and a cancel, and `to_kernel_rows` below is the one place where it and the
draft meet.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING

from adrobot.domain.errors import (
    OfferAlreadyInStreamError,
    OfferAlreadyRemovedError,
    OfferNotInStreamError,
    OfferNotRemovedError,
)
from adrobot.domain.ids import OfferId
from adrobot.domain.shares import OfferRow, redistribute, reject_duplicate_rows, replace_row

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


class DraftOperationKind(Enum):
    """The three edits the editor offers. Pinning is not among them: it changes no share."""

    ADD = "add"
    REMOVE = "remove"
    BRING_BACK = "bring_back"


@dataclass(frozen=True, slots=True, kw_only=True)
class DraftOperation:
    """One entry in the journal: what was done, and to which offer."""

    kind: DraftOperationKind
    offer_id: OfferId


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamDraft:
    """The rows of one stream as the user is arranging them.

    Carries no identity — which stream, which draft row in the database — on purpose: that
    belongs to the repository, and keeping it out is what lets the whole aggregate be
    exercised without one.

    `journal` is what this instance applied, in order. It is not reloaded from anywhere: the
    durable question "has this stream been edited" is answered by an open draft existing at
    all, and "what would the push change" by comparing these rows with the mirror.
    """

    rows: tuple[OfferRow, ...]
    journal: tuple[DraftOperation, ...] = ()

    @classmethod
    def opened(cls, rows: tuple[OfferRow, ...]) -> StreamDraft:
        """Open a draft on the stream as it stands, deliberately without recalculating.

        Opening is not an edit. The reference tool shows the tracker's own numbers until
        something is added or removed, even when they add up to 50 — normalising them here
        would be inventing data and would light up PUSH on a stream nobody touched.
        """
        reject_duplicate_rows(rows)
        return cls(rows=rows)

    def add(self, offer_id: OfferId) -> StreamDraft:
        """Put an offer into the stream as the most recently activated row.

        Refused when the stream already carries the offer, removed rows included: a removed
        row comes back through `bring_back`, and the two write different journal entries.
        The new row is free — a pin left in the mirror from a previous life of this row does
        not come back with it.
        """
        if any(row.offer_id == offer_id for row in self.rows):
            raise OfferAlreadyInStreamError(offer_id)
        added = OfferRow(
            offer_id=offer_id,
            seq=self._next_seq(),
            activated_at=self._next_stamp(),
        )
        return self._applied((*self.rows, added), DraftOperationKind.ADD, offer_id)

    def remove(self, offer_id: OfferId) -> StreamDraft:
        """Take a row out of the division, keeping it in place.

        The row is not dropped: the reference tool leaves it on screen, greyed out, at 0%,
        with BRING BACK live — and the push sends it explicitly as disabled.
        """
        if self._row(offer_id).removed:
            raise OfferAlreadyRemovedError(offer_id)
        rows = replace_row(self.rows, offer_id, lambda row: replace(row, removed=True))
        return self._applied(rows, DraftOperationKind.REMOVE, offer_id)

    def bring_back(self, offer_id: OfferId) -> StreamDraft:
        """Return a removed row to the division as the most recently activated one.

        The new stamp is the whole point of the verb: a row brought back takes the rounding
        remainder ahead of rows that have been there all along. That is state four from the
        video, where the oldest offer in the stream ends up with 38%.
        """
        if not self._row(offer_id).removed:
            raise OfferNotRemovedError(offer_id)
        stamp = self._next_stamp()
        rows = replace_row(
            self.rows, offer_id, lambda row: replace(row, removed=False, activated_at=stamp)
        )
        return self._applied(rows, DraftOperationKind.BRING_BACK, offer_id)

    def _applied(
        self, rows: tuple[OfferRow, ...], kind: DraftOperationKind, offer_id: OfferId
    ) -> StreamDraft:
        return StreamDraft(
            rows=redistribute(rows),
            journal=(*self.journal, DraftOperation(kind=kind, offer_id=offer_id)),
        )

    def _row(self, offer_id: OfferId) -> OfferRow:
        for row in self.rows:
            if row.offer_id == offer_id:
                return row
        raise OfferNotInStreamError(offer_id)

    def _next_seq(self) -> int:
        return max((row.seq for row in self.rows), default=0) + 1

    def _next_stamp(self) -> int:
        return max((row.activated_at for row in self.rows), default=0) + 1


def to_kernel_rows(rows: Iterable[OfferRow], pins: Mapping[OfferId, int]) -> tuple[OfferRow, ...]:
    """Join rows with the pins held for their stream, and the only place the two meet.

    `pins` decides: a row named there is pinned at that share, a row absent from it is free,
    whatever the row itself arrived carrying. Naming this mapper is the point — left
    unnamed, the join gets reinvented in the loader, the editor and the push.
    """
    return tuple(replace(row, pinned_share=pins.get(row.offer_id)) for row in rows)
