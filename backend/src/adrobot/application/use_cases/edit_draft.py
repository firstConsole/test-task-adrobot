"""The three buttons that change a flow's offers, and the one that throws the changes away.

ADD, REMOVE and BRING BACK are one scenario and not three. They differ by a single method
call on the aggregate; what they share is everything around it — taking the flow, opening a
draft over it if this is the first edit, recalculating, storing the rows and answering with
the flow as the screen will now draw it. Three scenarios would be three places for "when is
a draft opened" to be answered differently.

**A batch is all or nothing.** The operations are applied inside the transaction, so an
`add` the aggregate refuses on the fourth of five rolls the first three back with it. The
alternative — writing each as it succeeds — would leave the screen holding a flow that was
edited halfway by a request that answered 409.

**The draft is opened from the mirror exactly as the mirror stands, and never recalculated
on the way in.** Opening is not an edit: a flow whose shares sum to 50 keeps summing to 50
until somebody moves a row, which is the reference tool's own behaviour and the reason PUSH
stays dark on a flow nobody has touched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adrobot.application.errors import DraftBeingPushedError, StreamDoesNotRotateOffersError
from adrobot.application.use_cases.editor import offer_ids_of, stream_view
from adrobot.domain.diff import snapshot_hash
from adrobot.domain.draft import DraftOperationKind, DraftStatus, StreamDraft
from adrobot.domain.stream import StreamSchema

if TYPE_CHECKING:
    from adrobot.application.dto import StreamEditorView
    from adrobot.application.ports.persistence import (
        LiveDraft,
        MirroredStream,
        StreamView,
        Transaction,
        UnitOfWork,
    )
    from adrobot.domain.draft import DraftOperation
    from adrobot.domain.ids import CampaignId, KeitaroStreamId


def applied(draft: StreamDraft, operation: DraftOperation) -> StreamDraft:
    """Carry out one editor operation, returning the aggregate already recalculated.

    The one place the three verbs are told apart. Each refuses a state it does not fit —
    adding an offer the flow already carries, bringing back one that was never out — and
    those refusals are the domain's, raised with the offer named, which is what the screen
    puts under the row.
    """
    match operation.kind:
        case DraftOperationKind.ADD:
            return draft.add(operation.offer_id)
        case DraftOperationKind.REMOVE:
            return draft.remove(operation.offer_id)
        case DraftOperationKind.BRING_BACK:
            return draft.bring_back(operation.offer_id)


def refuse_a_flow_that_rotates_nothing(stream: MirroredStream) -> None:
    """Refuse an edit to a flow whose clicks never reach an offer at all."""
    if stream.schema is not StreamSchema.LANDINGS:
        raise StreamDoesNotRotateOffersError(stream.keitaro_stream_id, stream.schema.value)


def refuse_a_draft_in_flight(stream_id: KeitaroStreamId, draft: LiveDraft | None) -> None:
    """Refuse to touch a draft the push has already taken, which is what `pushing` means."""
    if draft is not None and draft.status is not DraftStatus.OPEN:
        raise DraftBeingPushedError(stream_id)


async def rendered(transaction: Transaction, view: StreamView) -> StreamEditorView:
    """Draw one flow for the answer, labelling its rows out of the catalogue."""
    return stream_view(view, await transaction.offers.by_ids(offer_ids_of((view,))))


class EditDraft:
    """ADD, REMOVE and BRING BACK, in one short transaction and no network call."""

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    async def __call__(
        self,
        *,
        campaign_id: CampaignId,
        stream_id: KeitaroStreamId,
        operations: tuple[DraftOperation, ...],
    ) -> StreamEditorView:
        """Stage these operations on one flow and answer with the flow as it now reads.

        Keyword-only rather than wrapped in a command object: the two identifiers are
        `NewType`s mypy already refuses to swap, and a keyword is what keeps the third
        argument from ever being handed in as the second.
        """
        async with self._uow.begin() as transaction:
            # FOR UPDATE before anything is decided: two requests editing one flow would
            # otherwise each seed a draft from the same mirror and the second would win
            # silently, having never seen the first one's rows.
            view = await transaction.streams.lock(campaign_id=campaign_id, stream_id=stream_id)
            refuse_a_flow_that_rotates_nothing(view.stream)
            refuse_a_draft_in_flight(stream_id, view.draft)
            if not operations:
                # Nothing to stage, so nothing is opened. A draft opened by an empty batch
                # would light PUSH and CANCEL up on a flow nobody had touched.
                return await rendered(transaction, view)
            draft = view.draft
            if draft is None:
                draft = await transaction.drafts.open_for(
                    campaign_id=campaign_id,
                    stream_id=stream_id,
                    rows=view.mirror_rows,
                    # Written once, here: recomputing it on a later edit would re-baseline
                    # the conflict check into something that can never fire.
                    base_snapshot_hash=snapshot_hash(view.mirror_rows),
                )
            edited = StreamDraft.opened(draft.rows)
            for operation in operations:
                edited = applied(edited, operation)
            await transaction.drafts.replace_rows(draft.id, edited.rows)
            # Read the flow back rather than assembling the answer out of what was just
            # written: this is the body the screen redraws itself from, and it should be the
            # same body a reload would fetch, proved rather than argued.
            return await rendered(
                transaction,
                await transaction.streams.lock(campaign_id=campaign_id, stream_id=stream_id),
            )


class DiscardDraft:
    """CANCEL: throw the staged edits away and leave the flow reading as the tracker holds it."""

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    async def __call__(
        self, *, campaign_id: CampaignId, stream_id: KeitaroStreamId
    ) -> StreamEditorView:
        """Close this flow's draft, or answer with the flow when there is none to close.

        Three things survive, and each is a decision rather than an omission:

        *   **The pins.** They live in the mirror precisely so that cancelling does not take
            them, which is what the reference tool does and the reason `offer_pins` is a
            table of its own.
        *   **The tracker's own shares, unnormalised.** A flow that summed to 50 before the
            first edit sums to 50 again afterwards. Cancelling undoes the recalculation
            along with the edit that caused it; leaving the flow at 100 would mean this
            service had quietly written something nobody asked for.
        *   **The draft row itself.** It is closed, never deleted — a push attempt points at
            it, and an audit trail with the middle torn out is not one.

        Silent on a flow with nothing staged: CANCEL is the same button pressed twice, and a
        second press is not a state worth a status code.
        """
        async with self._uow.begin() as transaction:
            view = await transaction.streams.lock(campaign_id=campaign_id, stream_id=stream_id)
            refuse_a_flow_that_rotates_nothing(view.stream)
            # Refused rather than obeyed: a push in flight has already told the tracker what
            # to hold, and discarding the draft behind it would leave nothing on this screen
            # that explains the state Keitaro is about to be in.
            refuse_a_draft_in_flight(stream_id, view.draft)
            if view.draft is not None:
                await transaction.drafts.change_status(
                    view.draft.id, was=DraftStatus.OPEN, becomes=DraftStatus.DISCARDED
                )
            return await rendered(
                transaction,
                await transaction.streams.lock(campaign_id=campaign_id, stream_id=stream_id),
            )
