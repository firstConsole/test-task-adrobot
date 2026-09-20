"""What a draft would change, in the two forms the answer is needed in.

For a person, before they press the button: what was added, what was taken out, whose share
moved. For the tracker, after they press it: the whole desired state of the stream.

Both come out of the same comparison, so they cannot disagree — and "is this draft dirty"
is answered here rather than by the aggregate, because a pin changes no row and therefore
must not light PUSH up.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from adrobot.domain.ids import OfferId
from adrobot.domain.values import OfferState

if TYPE_CHECKING:
    from collections.abc import Iterable

    from adrobot.domain.shares import OfferRow


@dataclass(frozen=True, slots=True, kw_only=True)
class DesiredOffer:
    """One row of the state a push asks the tracker to hold."""

    offer_id: OfferId
    share: int
    state: OfferState


@dataclass(frozen=True, slots=True, kw_only=True)
class ShareChange:
    """One offer's share, before and after."""

    offer_id: OfferId
    was: int
    now: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DraftDiff:
    """The difference between the stream as the tracker has it and as the draft wants it."""

    added: tuple[OfferId, ...]
    removed: tuple[OfferId, ...]
    brought_back: tuple[OfferId, ...]
    share_changes: tuple[ShareChange, ...]
    desired: tuple[DesiredOffer, ...]

    @classmethod
    def between(cls, mirror: tuple[OfferRow, ...], draft: tuple[OfferRow, ...]) -> DraftDiff:
        """Compare the mirror with the draft, in the draft's order.

        Assumes the draft was seeded from this mirror. A row the mirror has and the draft
        does not is left out of both the summary and the payload — it means the two were
        read at different times, which is what the snapshot hash is here to catch.
        """
        before = {row.offer_id: row for row in mirror}
        added: list[OfferId] = []
        removed: list[OfferId] = []
        brought_back: list[OfferId] = []
        share_changes: list[ShareChange] = []
        desired: list[DesiredOffer] = []

        for row in draft:
            was = before.get(row.offer_id)
            if was is None:
                if row.removed:
                    # Added and taken back again before any push: the tracker never heard of
                    # this row, so neither the summary nor the payload mentions it.
                    continue
                added.append(row.offer_id)
            elif was.removed and not row.removed:
                brought_back.append(row.offer_id)
            elif row.removed and not was.removed:
                removed.append(row.offer_id)
            elif row.share != was.share:
                share_changes.append(
                    ShareChange(offer_id=row.offer_id, was=was.share, now=row.share)
                )
            desired.append(
                DesiredOffer(
                    offer_id=row.offer_id,
                    share=0 if row.removed else row.share,
                    state=OfferState.DISABLED if row.removed else OfferState.ACTIVE,
                )
            )

        return cls(
            added=tuple(added),
            removed=tuple(removed),
            brought_back=tuple(brought_back),
            share_changes=tuple(share_changes),
            desired=tuple(desired),
        )

    @property
    def is_empty(self) -> bool:
        """Whether PUSH should stay dark. A pin moves no row, so a pin never lights it."""
        return not (self.added or self.removed or self.brought_back or self.share_changes)


def snapshot_hash(rows: Iterable[OfferRow]) -> str:
    """Fingerprint a stream's offer state, to notice it being edited in Keitaro meanwhile.

    Covers what the tracker owns — which offers, at what share, on or off — and nothing of
    ours: a pin or a row's position in the draft is not a reason to report a conflict.
    `hashlib` and not `hash()`, whose salt differs per process, so a stored value would
    never match again.
    """
    material = ";".join(
        f"{int(row.offer_id)}:{0 if row.removed else row.share}:{int(row.removed)}"
        for row in sorted(rows, key=lambda row: int(row.offer_id))
    )
    return hashlib.sha256(material.encode()).hexdigest()
