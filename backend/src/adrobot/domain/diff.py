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
        carried: list[OfferRow] = []

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
            carried.append(row)

        return cls(
            added=tuple(added),
            removed=tuple(removed),
            brought_back=tuple(brought_back),
            share_changes=tuple(share_changes),
            desired=desired_state(carried),
        )

    @property
    def is_empty(self) -> bool:
        """Whether PUSH should stay dark. A pin moves no row, so a pin never lights it."""
        return not (self.added or self.removed or self.brought_back or self.share_changes)

    @property
    def takes_no_traffic(self) -> bool:
        """Whether the state this would write leaves the flow with nowhere to send a click.

        `share > 0` as well as active: a row pinned at 0 is switched on and still receives
        nothing, and a flow of nothing but those is a flow that drops every click it is
        dispatched. The editor refuses to push one rather than discovering it in the
        statistics tomorrow.
        """
        return not any(row.state is OfferState.ACTIVE and row.share > 0 for row in self.desired)


def desired_state(rows: Iterable[OfferRow]) -> tuple[DesiredOffer, ...]:
    """Render rows as the state a push asks the tracker to hold, in the order given.

    The one rendering of it, used by the payload a push sends and by the picture a conflict
    shows of what the tracker holds instead — so the two sides of that comparison cannot be
    spelled differently and look like a difference that is not there.

    A removed row comes out explicitly, at 0 and disabled, rather than being left out: a row
    merely absent from the payload survives untouched on a build whose update merges arrays,
    and would go on taking traffic with the flow's shares summing past 100.
    """
    return tuple(
        DesiredOffer(
            offer_id=row.offer_id,
            share=0 if row.removed else row.share,
            state=OfferState.DISABLED if row.removed else OfferState.ACTIVE,
        )
        for row in rows
    )


def snapshot_hash(rows: Iterable[OfferRow]) -> str:
    """Fingerprint what a flow does with its traffic, to notice Keitaro being edited meanwhile.

    **Only the rows taking traffic are covered**, and that is the whole design of it. A row
    out of the rotation is a row the flow does not use, and whether the tracker keeps it
    disabled or has dropped it from the array altogether is a difference in bookkeeping
    rather than in behaviour — ours tombstones what theirs deletes. Hashing removed rows
    would make those two readings of one flow disagree for ever, so every push onto a flow
    somebody had tidied up in Keitaro would report a conflict that is not there, and nothing
    else would look wrong.

    Everything that *is* a change still moves it: a row switched off, one switched back on,
    one added, or any share that differs. Nothing of ours moves it — a pin, a row's position
    in the draft, the ordinals — because none of those is a reason to refuse a push.

    `hashlib` and not `hash()`, whose salt differs per process, so a stored value would never
    match again.
    """
    material = ";".join(
        f"{int(row.offer_id)}:{row.share}"
        for row in sorted(
            (row for row in rows if not row.removed), key=lambda row: int(row.offer_id)
        )
    )
    return hashlib.sha256(material.encode()).hexdigest()
