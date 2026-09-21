"""The campaign endpoints: create, adopt, list, finish, refresh, and the day's numbers.

Every handler is one line of work and no decisions. There is no `try/except` here and no
`HTTPException`: a failure is raised by the scenario in its own words and rendered by the
handlers in `api/errors.py`, which is the only place that knows what a failure is worth in
HTTP.

`dependencies=PROTECTED` is on the router and not on the handlers. One line guards six
endpoints, a seventh added below would inherit it, and the test that walks the OpenAPI
document fails if this line is ever the thing that gets deleted.

The status codes are the two the RFC makes worth distinguishing. Creating and adopting
answer **201**, because a campaign now exists in the tracker that did not before — and a
create whose flows failed still answers 201, because the campaign is exactly as created as
the body says it is. Finishing and refreshing answer **200**: the resource was already
there.

Statistics answer **200 whatever the report builder did**, which is the one place this file
departs from "the scenario raises and `api/errors.py` renders it". A tracker that will not
build a report has not failed this request: the campaign is here, the numbers are the part
that is missing, and the body says so in three fields. Answering 502 would put an error
toast over a working editor to report a dark column.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from adrobot.api.deps import (
    CreateCampaignDep,
    GetCampaignStatsDep,
    ImportCampaignDep,
    ListCampaignsDep,
    RepairCampaignDep,
    SyncCampaignDep,
)
from adrobot.api.schemas.campaigns import (
    CampaignResponse,
    CampaignsPageResponse,
    CampaignsQuery,
    CreateCampaignRequest,
    ImportCampaignRequest,
    decode_cursor,
)
from adrobot.api.schemas.problem import problem_responses
from adrobot.api.schemas.stats import CampaignStatsResponse
from adrobot.api.security import PROTECTED
from adrobot.application.dto import ListCampaignsQuery
from adrobot.domain.ids import CampaignId, KeitaroCampaignId

router = APIRouter(prefix="/api/v1/campaigns", tags=["campaigns"], dependencies=PROTECTED)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a campaign with its two flows",
    responses=problem_responses(401, 422, 502),
)
async def create_campaign(
    body: CreateCampaignRequest, create: CreateCampaignDep
) -> CampaignResponse:
    """Build the campaign part 1 describes, and report how far the tracker let us get."""
    return CampaignResponse.of(await create(body.to_command()))


@router.post(
    "/import",
    status_code=status.HTTP_201_CREATED,
    summary="Open a campaign the tracker already has",
    responses=problem_responses(401, 404, 409, 422, 502),
)
async def import_campaign(
    body: ImportCampaignRequest, adopt: ImportCampaignDep
) -> CampaignResponse:
    """Mirror an existing campaign and its flows, which is what part 2 opens."""
    return CampaignResponse.of(await adopt(KeitaroCampaignId(body.keitaro_campaign_id)))


@router.get(
    "",
    summary="List campaigns, newest first",
    responses=problem_responses(401, 422),
)
async def list_campaigns(
    query: Annotated[CampaignsQuery, Query()], campaigns: ListCampaignsDep
) -> CampaignsPageResponse:
    """Read one page. `after` is the `next_cursor` of the page before it, unread."""
    return CampaignsPageResponse.of(
        await campaigns(
            ListCampaignsQuery(
                # Already proved readable by the model's own validator, which is what makes
                # a token nobody issued a 422 about `query.after` rather than a 500 here.
                after=None if query.after is None else decode_cursor(query.after),
                query=query.q,
                limit=query.limit,
            )
        )
    )


@router.post(
    "/{campaign_id}/repair",
    summary="Finish a campaign whose flows were never built",
    responses=problem_responses(401, 404, 409, 502),
)
async def repair_campaign(campaign_id: UUID, repair: RepairCampaignDep) -> CampaignResponse:
    """Create whatever of the two flows the tracker does not have. Safe to press twice."""
    return CampaignResponse.of(await repair(CampaignId(campaign_id)))


@router.post(
    "/{campaign_id}/refetch",
    summary="Read this campaign and its flows from the tracker again",
    responses=problem_responses(401, 404, 502),
)
async def refetch_campaign(campaign_id: UUID, sync: SyncCampaignDep) -> CampaignResponse:
    """FETCH STREAMS FROM KT. What the tracker no longer returns is kept and marked absent."""
    return CampaignResponse.of(await sync(CampaignId(campaign_id)))


@router.get(
    "/{campaign_id}/stats",
    summary="Read this campaign's clicks today, by flow and by offer",
    responses=problem_responses(401, 404),
)
async def read_campaign_stats(
    campaign_id: UUID, statistics: GetCampaignStatsDep
) -> CampaignStatsResponse:
    """Two reports behind one answer, cached for the whole process for forty-five seconds.

    No 502 in the list above, and that is the endpoint's contract rather than an oversight:
    a report the tracker would not build comes back as `available: false` with a reason.
    """
    return CampaignStatsResponse.of(await statistics(CampaignId(campaign_id)))
