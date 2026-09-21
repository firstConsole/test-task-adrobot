"""PUSH TO KT: three phases, and a database transaction that never spans the tracker call.

The shape is the whole point, and the tempting version — `async with uow.begin(): await
admin.push(...)` — is the one that empties the connection pool the first time Keitaro is
slow. So:

1.  **A short transaction.** Take the flow, decide what to write, open an audit row and mark
    the draft `pushing`. Commit.
2.  **No transaction at all.** Write the flow whole, read it back, compare. This is the part
    that can take ten seconds.
3.  **A short transaction.** Mirror what came back, close the draft, close the audit row.

Three details, each of which is a defect in the version that leaves it out:

*   **The desired state is what the screen showed, recomputed by nothing.** PLAN-BACKEND §6
    sketches a second `redistribute()` here; it is deliberately not run. It would be a no-op
    in every case but one — a pin set after the last edit — and in that one it would send
    Keitaro numbers the screen never displayed. The single quality bar this project is held
    to is that the figures agree with the tracker, and a recalculation nobody can see is the
    exact shape of failing it.
*   **A removed row travels explicitly**, at share 0 and disabled, because a row merely left
    out of the payload survives untouched on a build whose update merges arrays — still
    taking traffic, with the flow's shares now summing past 100. `DraftDiff.desired` is
    where that is spelled.
*   **A failed push is an event, not a state.** Whatever happens out there, the draft goes
    back to `open` with its rows exactly as they were, and the audit row records which kind
    of failure it was. Losing somebody's edits to a bad minute on the network is not a
    trade this service makes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from adrobot.application.errors import (
    DraftBeingPushedError,
    NothingToPushError,
    PushBlockedError,
    UpstreamError,
    UpstreamProtocolError,
    UpstreamUnavailableError,
)
from adrobot.application.push import PushOutcome
from adrobot.application.use_cases.edit_draft import (
    refuse_a_flow_that_rotates_nothing,
    rendered,
)
from adrobot.application.use_cases.editor import block_reason
from adrobot.domain.diff import DesiredOffer, DraftDiff
from adrobot.domain.draft import DraftStatus
from adrobot.domain.ids import DraftId, PushAttemptId

if TYPE_CHECKING:
    from adrobot.application.dto import StreamEditorView
    from adrobot.application.ports.keitaro import KeitaroAdminPort
    from adrobot.application.ports.persistence import LiveDraft, Transaction, UnitOfWork
    from adrobot.application.ports.system import Clock, CorrelationIds
    from adrobot.domain.ids import CampaignId, KeitaroStreamId
    from adrobot.domain.stream import Stream

WEDGED_AFTER: Final = timedelta(minutes=2)
"""How long a draft may sit `pushing` before another request may take the push over.

A push is three tracker calls against a ten-second read timeout with at most three attempts
each, so a live one finishes far inside this. What it protects against is the process that
died between phase 1 and phase 3: without it that flow is `pushing` for ever, which is a
flow nobody can edit, push or cancel until somebody runs SQL by hand."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ClaimedPush:
    """What phase 1 decided, carried across the tracker call to phase 3.

    Three identifiers and a payload, and deliberately not the `StreamView` they came from:
    that view describes a flow as it was before the write, and holding it across the call
    would invite phase 3 to answer out of it.
    """

    draft_id: DraftId
    attempt_id: PushAttemptId
    desired: tuple[DesiredOffer, ...]


def outcome_of(failure: UpstreamError) -> PushOutcome:
    """Classify what phase 2 ran into, in the terms the audit row is read back in.

    `INDETERMINATE` is the one that earns its place: the request left and no answer came, so
    the write may or may not have landed. Nothing retries blindly on it — the payload is a
    whole desired state, so the safe move is to look, and looking is what pressing PUSH
    again does.
    """
    if isinstance(failure, UpstreamProtocolError):
        return PushOutcome.MISMATCHED
    if isinstance(failure, UpstreamUnavailableError) and failure.status is None:
        return PushOutcome.INDETERMINATE
    return PushOutcome.FAILED


class PushDraft:
    """One press of PUSH TO KT, from the flow on screen to the flow the tracker then holds."""

    def __init__(
        self,
        *,
        admin: KeitaroAdminPort,
        uow: UnitOfWork,
        clock: Clock,
        correlation: CorrelationIds,
    ) -> None:
        self._admin = admin
        self._uow = uow
        self._clock = clock
        self._correlation = correlation

    async def __call__(
        self, *, campaign_id: CampaignId, stream_id: KeitaroStreamId
    ) -> StreamEditorView:
        """Write this flow's draft to the tracker and answer with the flow as it now reads.

        The three phases are the three statements below, and the `try` is what makes the
        middle one survivable: a tracker that refuses, times out or writes something else
        hands the draft back to its owner rather than stranding it.
        """
        claimed = await self._claim(campaign_id=campaign_id, stream_id=stream_id)
        try:
            written = await self._admin.replace_stream_offers(stream_id, claimed.desired)
        except UpstreamError as failure:
            await self._hand_back(claimed, outcome_of(failure))
            raise
        return await self._settle(
            campaign_id=campaign_id, stream_id=stream_id, claimed=claimed, written=written
        )

    async def _claim(self, *, campaign_id: CampaignId, stream_id: KeitaroStreamId) -> ClaimedPush:
        """Phase 1: take the flow, work out what to write, and claim the push.

        Everything that can refuse the push refuses it here, before the tracker has been
        touched and while the flow is still locked — so two requests pressing PUSH together
        cannot both come away believing they own it.
        """
        async with self._uow.begin() as transaction:
            view = await transaction.streams.lock(campaign_id=campaign_id, stream_id=stream_id)
            refuse_a_flow_that_rotates_nothing(view.stream)
            draft = view.draft
            if draft is None:
                raise NothingToPushError(stream_id)
            if draft.status is not DraftStatus.OPEN:
                await self._abandon_a_wedged_push(transaction, stream_id, draft)
                draft = replace(draft, status=DraftStatus.OPEN)
                view = replace(view, draft=draft)
            # The rows exactly as the last edit left them. See the module docstring for why
            # they are not divided a second time here.
            diff = DraftDiff.between(view.mirror_rows, draft.rows)
            blocked = block_reason(view, diff)
            if blocked is not None:
                # The same sentence the screen already had on the dark button, so a client
                # that pressed it anyway is told what it was already being told.
                raise PushBlockedError(blocked)
            if diff.is_empty:
                raise NothingToPushError(stream_id)
            attempt_id = await transaction.pushes.start(
                draft_id=draft.id,
                desired=diff.desired,
                correlation_id=self._correlation.current(),
            )
            # Last, and inside the same transaction as the audit row: a draft marked
            # `pushing` with no attempt beside it is a draft nothing can date, and dating it
            # is the whole of how a wedged push is told from a live one.
            await transaction.drafts.change_status(
                draft.id, was=DraftStatus.OPEN, becomes=DraftStatus.PUSHING
            )
            return ClaimedPush(draft_id=draft.id, attempt_id=attempt_id, desired=diff.desired)

    async def _abandon_a_wedged_push(
        self, transaction: Transaction, stream_id: KeitaroStreamId, draft: LiveDraft
    ) -> None:
        """Take over a push whose process died, or refuse one that is simply still running.

        The attempt row is what dates it — there is no `pushing_since` column, and this is
        why. An attempt still in flight and younger than `WEDGED_AFTER` is somebody's live
        push and this request waits; anything else is wreckage, recorded as
        `indeterminate` because nobody knows whether that write landed.
        """
        attempt = await transaction.pushes.latest_for(draft.id)
        if attempt is not None and attempt.outcome is PushOutcome.IN_FLIGHT:
            if self._clock.now() - attempt.started_at < WEDGED_AFTER:
                raise DraftBeingPushedError(stream_id)
            await transaction.pushes.settle(attempt.id, PushOutcome.INDETERMINATE)
        await transaction.drafts.change_status(
            draft.id, was=DraftStatus.PUSHING, becomes=DraftStatus.OPEN
        )

    async def _hand_back(self, claimed: ClaimedPush, outcome: PushOutcome) -> None:
        """Phase 3 for a push that never landed: close the attempt, give the draft back.

        The rows are untouched, so the edits are still on screen and the button is still
        live. The attempt is settled first on purpose: if somebody has taken this push over
        in the meantime, that call raises and this one stops rather than rewriting the
        history of a push it no longer owns.
        """
        async with self._uow.begin() as transaction:
            await transaction.pushes.settle(claimed.attempt_id, outcome)
            await transaction.drafts.change_status(
                claimed.draft_id, was=DraftStatus.PUSHING, becomes=DraftStatus.OPEN
            )

    async def _settle(
        self,
        *,
        campaign_id: CampaignId,
        stream_id: KeitaroStreamId,
        claimed: ClaimedPush,
        written: Stream,
    ) -> StreamEditorView:
        """Phase 3: mirror what the tracker gave back, close the draft, close the attempt.

        `upsert_stream_offers` tombstones what the answer no longer carries rather than
        deleting it, which is what leaves a removed row on screen afterwards — grey, at 0%,
        with BRING BACK live. That is the single most recognisable thing the reference tool
        does, and a mirror write that replaced instead of tombstoning would lose it while
        every other test still passed.
        """
        at = self._clock.now()
        async with self._uow.begin() as transaction:
            await transaction.streams.upsert_stream_offers(
                campaign_id=campaign_id, stream_id=stream_id, offers=written.offers, at=at
            )
            await transaction.drafts.change_status(
                claimed.draft_id, was=DraftStatus.PUSHING, becomes=DraftStatus.PUSHED
            )
            await transaction.pushes.settle(claimed.attempt_id, PushOutcome.APPLIED)
            return await rendered(
                transaction,
                await transaction.streams.lock(campaign_id=campaign_id, stream_id=stream_id),
            )
