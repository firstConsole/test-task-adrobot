"""Fixing one row's share, and letting it go again.

The pin is the odd one out of the editor's buttons, and every surprising thing about it
follows from one decision: **it lives in the mirror and not in the draft.** In the reference
tool a pin survives both a push and a cancel, so it cannot be staged with the edits that are
thrown away by either.

Three consequences, each of which looks like a bug until this is read:

*   **Pinning recalculates nothing.** The shares on screen do not move when a row is pinned;
    the pin only decides what happens at the *next* edit. `domain/shares.pin()` says the
    same thing from the other side.
*   **Pinning does not dirty the flow.** No draft is opened, PUSH stays dark, and a flow
    with pins and no edits is a clean flow.
*   **A push in flight is no reason to refuse one.** A push computed its desired state in
    phase 1 and is only waiting for the tracker; a pin set meanwhile changes nothing it is
    about to write, and it was always going to survive the push anyway.

What the server does insist on is the number. This is the one share a client can influence
at all — the whole point of there being no endpoint that accepts rows with shares filled in
— so it is validated here, by the same function that divides the rest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adrobot.application.use_cases.edit_draft import refuse_a_flow_that_rotates_nothing
from adrobot.application.use_cases.editor import rendered, rows_on_screen
from adrobot.domain.shares import pin, redistribute
from adrobot.domain.values import Share

if TYPE_CHECKING:
    from adrobot.application.dto import StreamEditorView
    from adrobot.application.ports.persistence import StreamView, UnitOfWork
    from adrobot.domain.ids import CampaignId, KeitaroStreamId, OfferId


def refuse_a_pin_that_leaves_nothing_to_divide(
    view: StreamView, offer_id: OfferId, share: Share | None
) -> None:
    """Refuse a pin the arithmetic could not honour, and never touch the shares on screen.

    Two domain calls and no arithmetic here. `pin` builds the flow as it would stand —
    raising if the flow has no such row — and `redistribute` is asked to divide it purely so
    that it can object; its answer is dropped on the floor, because a pin must move no share
    until the next edit.

    It objects only about the rows that are *active*: a pin held on a removed row reserves
    nothing, so pinning one is allowed and it is BRING BACK that may later find the pins
    over-subscribed. Refusing it here instead would rule out the reference tool's own fourth
    state, where the row that comes back is the one the remainder goes to.
    """
    redistribute(pin(rows_on_screen(view), offer_id, at=None if share is None else int(share)))


class SetOfferPin:
    """PIN: hold one row at a share, at the number asked for or at the one it already shows."""

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    async def __call__(
        self,
        *,
        campaign_id: CampaignId,
        stream_id: KeitaroStreamId,
        offer_id: OfferId,
        share: Share | None = None,
    ) -> StreamEditorView:
        """Pin one row and answer with the flow, whose shares are deliberately unchanged.

        `share=None` is the button as the screen offers it — hold this row where it is. A
        number is the dialog behind it, and it is the only number this API takes from a
        client at all.
        """
        async with self._uow.begin() as transaction:
            view = await transaction.streams.lock(campaign_id=campaign_id, stream_id=stream_id)
            refuse_a_flow_that_rotates_nothing(view.stream)
            refuse_a_pin_that_leaves_nothing_to_divide(view, offer_id, share)
            await transaction.pins.hold(
                campaign_id=campaign_id,
                stream_id=stream_id,
                offer_id=offer_id,
                # The row's own share where none was asked for. Read off the draft when one
                # is live and off the mirror otherwise, which is the number on screen.
                share=share if share is not None else _shown(view, offer_id),
            )
            return await rendered(
                transaction,
                await transaction.streams.lock(campaign_id=campaign_id, stream_id=stream_id),
            )


class ReleaseOfferPin:
    """The same button pressed again: the row goes back into the division at the next edit."""

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    async def __call__(
        self, *, campaign_id: CampaignId, stream_id: KeitaroStreamId, offer_id: OfferId
    ) -> StreamEditorView:
        """Release the pin if there is one, and say nothing when there is not.

        Idempotent on purpose: presence of the row is the pin, so a second press is not a
        state worth a status code — and nothing is recalculated here either, for the same
        reason pinning does not.
        """
        async with self._uow.begin() as transaction:
            view = await transaction.streams.lock(campaign_id=campaign_id, stream_id=stream_id)
            refuse_a_flow_that_rotates_nothing(view.stream)
            await transaction.pins.release(
                campaign_id=campaign_id, stream_id=stream_id, offer_id=offer_id
            )
            return await rendered(
                transaction,
                await transaction.streams.lock(campaign_id=campaign_id, stream_id=stream_id),
            )


def _shown(view: StreamView, offer_id: OfferId) -> Share:
    """Return the share this row draws right now, which the guard above proved exists."""
    return Share(next(row.share for row in rows_on_screen(view) if row.offer_id == offer_id))
