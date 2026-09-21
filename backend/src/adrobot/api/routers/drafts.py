"""The four buttons that stage, rehearse, send and abandon a flow's edits.

All four answer with the flow as it now reads, and never with a bare status: the screen
redraws itself from the body, and an answer that only said "ok" would force a second request
to find out what the shares became — which is exactly the moment two implementations of the
arithmetic would start to disagree.

**None of them accepts a share.** The client says which offer, never at what percentage. The
one exception is the pin, which lives on the flow router next door and is validated by the
same function that divides everything else.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from adrobot.api.deps import (
    DiscardDraftDep,
    EditDraftDep,
    GetStreamViewDep,
    PushDraftDep,
    SettingsDep,
)
from adrobot.api.schemas.problem import problem_responses
from adrobot.api.schemas.streams import EditDraftRequest, PushDraftRequest, StreamResponse
from adrobot.api.security import PROTECTED
from adrobot.domain.draft import DraftOperation
from adrobot.domain.ids import CampaignId, KeitaroStreamId, OfferId

router = APIRouter(
    prefix="/api/v1/campaigns/{campaign_id}/streams/{stream_id}/draft",
    tags=["editor"],
    dependencies=PROTECTED,
)


@router.post(
    "/operations",
    summary="Stage add, remove and bring back on this flow",
    responses=problem_responses(401, 404, 409, 422),
)
async def edit_draft(
    campaign_id: UUID,
    stream_id: int,
    body: EditDraftRequest,
    edit: EditDraftDep,
    settings: SettingsDep,
) -> StreamResponse:
    """Apply a batch of edits. All or nothing: a refusal leaves the flow exactly as it was."""
    return StreamResponse.of(
        await edit(
            campaign_id=CampaignId(campaign_id),
            stream_id=KeitaroStreamId(stream_id),
            operations=tuple(
                DraftOperation(kind=row.kind, offer_id=OfferId(row.offer_id))
                for row in body.operations
            ),
        ),
        tracker=str(settings.keitaro_public_base_url),
    )


@router.get(
    "/preview",
    summary="Show what pressing PUSH would send, without sending it",
    responses=problem_responses(401, 404, 409),
)
async def preview_draft(
    campaign_id: UUID, stream_id: int, flow: GetStreamViewDep, settings: SettingsDep
) -> StreamResponse:
    """Rehearse the push: `diff.desired` is the payload it would send, not a description."""
    return StreamResponse.of(
        await flow(campaign_id=CampaignId(campaign_id), stream_id=KeitaroStreamId(stream_id)),
        tracker=str(settings.keitaro_public_base_url),
    )


@router.post(
    "/push",
    summary="Write this flow's draft to the tracker",
    responses=problem_responses(401, 404, 409, 502),
)
async def push_draft(
    campaign_id: UUID,
    stream_id: int,
    body: PushDraftRequest,
    push: PushDraftDep,
    settings: SettingsDep,
) -> StreamResponse:
    """PUSH TO KT. A 409 carrying `conflict` means somebody edited the flow in Keitaro."""
    return StreamResponse.of(
        await push(
            campaign_id=CampaignId(campaign_id),
            stream_id=KeitaroStreamId(stream_id),
            overwrite=body.overwrite,
        ),
        tracker=str(settings.keitaro_public_base_url),
    )


@router.delete(
    "",
    summary="Throw this flow's staged edits away",
    responses=problem_responses(401, 404, 409),
)
async def discard_draft(
    campaign_id: UUID, stream_id: int, discard: DiscardDraftDep, settings: SettingsDep
) -> StreamResponse:
    """CANCEL. The pins survive it, and so do the tracker's own unnormalised shares."""
    return StreamResponse.of(
        await discard(campaign_id=CampaignId(campaign_id), stream_id=KeitaroStreamId(stream_id)),
        tracker=str(settings.keitaro_public_base_url),
    )
