"""The editor's read, and the pin.

`{stream_id}` is the flow's id **in Keitaro** — 564221, the number printed in the group
heading and the one a reviewer compares against the tracker. It is always scoped by the
campaign in the same SQL predicate, so a URL naming another campaign's flow finds nothing
and answers 404; there is no unscoped lookup behind any of these paths to reach for.

The pin is a `PUT` and a `DELETE` on a subresource rather than a field of the flow, because
presence of the row *is* the pin: there is no "pinned: false" to send, and both verbs are
idempotent by construction.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from adrobot.api.deps import (
    GetEditorViewDep,
    ReleaseOfferPinDep,
    SetOfferPinDep,
    SettingsDep,
)
from adrobot.api.schemas.problem import problem_responses
from adrobot.api.schemas.streams import PinOfferRequest, StreamResponse, StreamsResponse
from adrobot.api.security import PROTECTED
from adrobot.domain.ids import CampaignId, KeitaroStreamId, OfferId
from adrobot.domain.values import Share

router = APIRouter(
    prefix="/api/v1/campaigns/{campaign_id}/streams", tags=["editor"], dependencies=PROTECTED
)


@router.get(
    "",
    summary="Read one campaign's flows as the editor draws them",
    responses=problem_responses(401, 404),
)
async def read_streams(
    campaign_id: UUID, editor: GetEditorViewDep, settings: SettingsDep
) -> StreamsResponse:
    """Draw the whole screen in one answer: the mirror, the live draft over it, the pins."""
    return StreamsResponse.of(
        await editor(CampaignId(campaign_id)),
        tracker=str(settings.keitaro_public_base_url),
    )


@router.put(
    "/{stream_id}/offers/{offer_id}/pin",
    summary="Hold one row's share where it is",
    responses=problem_responses(401, 404, 409, 422),
)
async def pin_offer(  # noqa: PLR0913, PLR0917  # three path segments, a body and two dependencies
    campaign_id: UUID,
    stream_id: int,
    offer_id: int,
    body: PinOfferRequest,
    pin: SetOfferPinDep,
    settings: SettingsDep,
) -> StreamResponse:
    """Pin a row. Deliberately moves no other share and lights no button — see the use case."""
    return StreamResponse.of(
        await pin(
            campaign_id=CampaignId(campaign_id),
            stream_id=KeitaroStreamId(stream_id),
            offer_id=OfferId(offer_id),
            share=None if body.share is None else Share(body.share),
        ),
        tracker=str(settings.keitaro_public_base_url),
    )


@router.delete(
    "/{stream_id}/offers/{offer_id}/pin",
    summary="Let one row back into the division",
    responses=problem_responses(401, 404, 409),
)
async def unpin_offer(
    campaign_id: UUID,
    stream_id: int,
    offer_id: int,
    release: ReleaseOfferPinDep,
    settings: SettingsDep,
) -> StreamResponse:
    """Release the pin if there is one. Silent when there is not: a second press is not an error."""
    return StreamResponse.of(
        await release(
            campaign_id=CampaignId(campaign_id),
            stream_id=KeitaroStreamId(stream_id),
            offer_id=OfferId(offer_id),
        ),
        tracker=str(settings.keitaro_public_base_url),
    )
