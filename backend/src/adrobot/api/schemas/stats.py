"""The Stats column on the wire: one campaign's clicks today, and how much to believe them.

**Three fields say what condition the numbers are in, and a client that ignores all three
draws a lie.** `available` false is not an empty column — it is a column that was never
filled, and a screen that renders it as zeroes tells a media buyer an offer took no traffic
when the truth is that nobody asked. `stale` true means the rows are real but older than
they should be, and `read_at` is the moment to print beside them. Decided here rather than
left to arithmetic on the client for the same reason the shares are: two implementations of
"is this current" is one implementation too many.

`day` and `timezone` are the caption. Keitaro serialises its timestamps without an offset,
in its own zone, so "clicks today" is not checkable until both are printed — and for several
hours of every day the tracker's today and the reader's are different dates.

Rows and not an object keyed by id, although the screen looks each one up by id: a JSON
object whose keys are stringified integers generates as `Record<string, number>`, which
types nothing, and the page builds its map once either way.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from adrobot.application.dto import CampaignStats, OfferClicks, StreamClicks


class StreamStatsResponse(BaseModel):
    """What one flow took today — the number the editor prints in the group heading."""

    model_config = ConfigDict(frozen=True)

    keitaro_stream_id: int
    clicks: int = 0

    @classmethod
    def of(cls, row: StreamClicks) -> StreamStatsResponse:
        """Render one flow's line, which is the only way this model is built."""
        return cls(keitaro_stream_id=int(row.keitaro_stream_id), clicks=row.clicks)


class OfferStatsResponse(BaseModel):
    """What one offer did today — one cell of the editor's Stats column."""

    model_config = ConfigDict(frozen=True)

    offer_id: int
    clicks: int = 0
    conversions: int = 0

    @classmethod
    def of(cls, row: OfferClicks) -> OfferStatsResponse:
        """Render one offer's line, which is the only way this model is built."""
        return cls(offer_id=int(row.offer_id), clicks=row.clicks, conversions=row.conversions)


class CampaignStatsResponse(BaseModel):
    """One campaign's numbers for one day, with everything needed to caption them.

    A flow or an offer the report did not mention has no row at all, and that is deliberate:
    a zero written here would be this service inventing a measurement. The screen draws an
    empty cell, which is what "the tracker said nothing about this" looks like.
    """

    model_config = ConfigDict(frozen=True)

    day: date = Field(description="The tracker's day, not this service's.")
    timezone: str = Field(description="The IANA zone `day` was worked out in.")
    available: bool = Field(
        description=(
            "Whether these rows are the tracker's numbers at all. False is not an empty "
            "day: a campaign with no traffic is available with no rows."
        )
    )
    stale: bool = Field(
        default=False,
        description="These are the last numbers that could be read, not today's latest.",
    )
    read_at: datetime | None = Field(
        default=None, description="When the tracker answered, and null when it never has."
    )
    unavailable_reason: str | None = Field(
        default=None, description="Why the last attempt failed, in words a buyer reads."
    )
    streams: tuple[StreamStatsResponse, ...] = ()
    offers: tuple[OfferStatsResponse, ...] = ()

    @classmethod
    def of(cls, stats: CampaignStats) -> CampaignStatsResponse:
        """Render one reading exactly as it was assembled, deciding nothing of its own."""
        return cls(
            day=stats.day,
            timezone=stats.timezone,
            available=stats.available,
            stale=stats.stale,
            read_at=stats.read_at,
            unavailable_reason=stats.unavailable_reason,
            streams=tuple(StreamStatsResponse.of(row) for row in stats.streams),
            offers=tuple(OfferStatsResponse.of(row) for row in stats.offers),
        )
