"""The offer catalogue: the combobox behind ADD, and the button that refreshes it.

There is a local catalogue because `GET /offers` in Keitaro takes no query parameters at
all — no search, no paging, no filter. Searching it here is the only way to answer a person
typing `11104` into a box.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from adrobot.api.deps import SearchOffersDep, SettingsDep, SyncOfferCatalogueDep
from adrobot.api.schemas.offers import (
    OfferCatalogueSyncResponse,
    OfferResponse,
    OffersResponse,
)
from adrobot.api.schemas.problem import problem_responses
from adrobot.api.security import PROTECTED
from adrobot.application.dto import DEFAULT_OFFER_LIMIT, MAX_OFFER_LIMIT

router = APIRouter(prefix="/api/v1/offers", tags=["offers"], dependencies=PROTECTED)


class OffersQuery(BaseModel):
    """The combobox's query string, as one model so that an unknown parameter is refused."""

    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(
        default=None, description="Matched against the offer id as a prefix, and the name."
    )
    limit: int = Field(default=DEFAULT_OFFER_LIMIT, ge=1, le=MAX_OFFER_LIMIT)


@router.get(
    "",
    summary="Search the mirrored offer catalogue",
    responses=problem_responses(401, 422),
)
async def search_offers(
    query: Annotated[OffersQuery, Query()], offers: SearchOffersDep, settings: SettingsDep
) -> OffersResponse:
    """No text is SHOW ALL OFFERS. An id prefix outranks a name that merely contains it."""
    tracker = str(settings.keitaro_public_base_url)
    return OffersResponse(
        offers=tuple(
            OfferResponse.of(offer, tracker=tracker)
            for offer in await offers(query=query.q, limit=query.limit)
        )
    )


@router.post(
    "/sync",
    summary="Read the tracker's whole offer list again",
    responses=problem_responses(401, 502),
)
async def sync_offers(refresh: SyncOfferCatalogueDep) -> OfferCatalogueSyncResponse:
    """`synced_at` is null when the tracker listed nothing: the copy was left alone."""
    return OfferCatalogueSyncResponse.of(await refresh())
