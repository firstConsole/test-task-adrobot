"""The editor's read: one campaign's flows, as the screen draws them.

Nothing here writes, and everything that writes reads through it. `EditDraft`, the pin and
the push all answer with a `StreamEditorView` assembled by the functions below, so a flow
looks the same however the caller arrived at it — which is what stops "the row order after
an add" and "the row order after a reload" from being two different rules.

Three of those functions are deliberately not private. They are the answer to questions the
push has to ask as well — what would this write, and may it be written — and a second
spelling of either is a second opinion about the only thing this project is judged on.

The catalogue is read once for the whole screen and not once per flow: an offer id is a
label, and two flows of one campaign usually share most of theirs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adrobot.application.dto import CampaignView, EditorRow, EditorView, StreamEditorView
from adrobot.domain.diff import DraftDiff, snapshot_hash
from adrobot.domain.draft import DraftStatus
from adrobot.domain.shares import display_order

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from adrobot.application.ports.persistence import StreamView, UnitOfWork
    from adrobot.domain.ids import CampaignId, OfferId
    from adrobot.domain.offer import Offer
    from adrobot.domain.shares import OfferRow


def rows_on_screen(view: StreamView) -> tuple[OfferRow, ...]:
    """Return the rows the editor shows: the draft's while one is live, the mirror's otherwise.

    The mirror is never blended into a live draft. A flow refetched from Keitaro while
    somebody was editing it is a conflict to be reported at the push, not a set of rows to
    be quietly merged into their work.
    """
    return view.mirror_rows if view.draft is None else view.draft.rows


def diff_of(view: StreamView) -> DraftDiff | None:
    """Return what this flow's draft would change, `None` where there is no draft to compare."""
    return None if view.draft is None else DraftDiff.between(view.mirror_rows, view.draft.rows)


def block_reason(view: StreamView, diff: DraftDiff | None) -> str | None:
    """Why PUSH is dark although this flow has a draft, in the words a media buyer reads.

    `None` also when there is simply nothing to push: the button is not rendered on a clean
    flow at all, and a reason attached to a button nobody can see is a sentence that ends up
    printed somewhere it makes no sense.
    """
    draft = view.draft
    if draft is None or diff is None:
        return None
    if draft.status is not DraftStatus.OPEN:
        return "this flow is being pushed right now — wait for that to finish"
    if diff.is_empty:
        return None
    if view.stream.absent:
        return (
            f"Keitaro no longer returns flow {view.stream.keitaro_stream_id} — "
            f"fetch this campaign from the tracker before pushing"
        )
    if diff.takes_no_traffic:
        return f"{view.stream.name} would have no active offer left — its traffic would go nowhere"
    return None


def warnings_for(view: StreamView) -> tuple[str, ...]:
    """Say what is worth saying about this flow without stopping the push.

    One warning so far, and it is the one the conflict check turns into a refusal at the
    tracker: the mirror has moved since the draft was opened, so what is on screen was
    diffed against a state Keitaro may no longer hold. Said here as a warning because a
    fetch is somebody's own doing, and refusing a push on the strength of our own cache
    would be this service second-guessing the button it just offered.
    """
    draft = view.draft
    if draft is None or draft.base_snapshot_hash == snapshot_hash(view.mirror_rows):
        return ()
    return (
        (
            "this flow has been fetched from Keitaro since the draft was opened — "
            "pushing will overwrite what the tracker holds now"
        ),
    )


def stream_view(view: StreamView, labels: Mapping[OfferId, Offer]) -> StreamEditorView:
    """Assemble one flow for the screen, rows already ordered and shares already decided."""
    diff = diff_of(view)
    blocked = block_reason(view, diff)
    return StreamEditorView(
        stream=view.stream,
        rows=tuple(
            EditorRow(
                offer_id=row.offer_id,
                offer=labels.get(row.offer_id),
                share=row.share,
                pinned_share=row.pinned_share,
                removed=row.removed,
            )
            # Sorted here and nowhere else. `display_order` floats the row that took the
            # rounding remainder to the top, so a client that re-sorted by anything would
            # hide the one thing a reviewer compares against Keitaro.
            for row in display_order(rows_on_screen(view))
        ),
        dirty=view.draft is not None,
        diff=diff,
        can_push=diff is not None and not diff.is_empty and blocked is None,
        block_reason=blocked,
        warnings=warnings_for(view),
    )


def offer_ids_of(views: Iterable[StreamView]) -> set[OfferId]:
    """Every offer id a screenful of flows mentions, the staged additions included.

    The draft's rows as well as the mirror's: an offer added a moment ago is not in the
    mirror yet, and it is the row whose label the person is most likely to be looking at.
    """
    return {row.offer_id for view in views for row in (*view.mirror_rows, *rows_on_screen(view))}


class GetEditorView:
    """The whole editor screen for one campaign, in one transaction and three statements."""

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    async def __call__(self, campaign_id: CampaignId) -> EditorView:
        """Read the campaign, its flows and the labels for their rows, or raise on the campaign.

        One transaction, because the three reads describe one screen: a campaign read
        outside it could be refetched between the statements and the heading would then
        describe a state the rows below it no longer show.
        """
        async with self._uow.begin() as transaction:
            campaign = await transaction.campaigns.get(campaign_id)
            views = await transaction.streams.views_for(campaign_id)
            labels = await transaction.offers.by_ids(offer_ids_of(views))
        return EditorView(
            campaign=CampaignView.of(campaign),
            streams=tuple(stream_view(view, labels) for view in views),
        )
