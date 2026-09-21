"""Today's numbers, and what the screen shows when they cannot be read.

`/report/build` is the most expensive endpoint the tracker has and the editor asks for it
again on every redraw, so the two things this decides are how often it is really asked and
what happens on the answer it fails to give.

**The window is forty-five seconds** (PLAN-BACKEND: thirty to sixty). Short enough that a
click arriving during a demonstration shows up while somebody is still looking at the
screen, long enough that a person clicking through four campaigns and back pays for one
round of reports rather than eight. The figure is a constant and not an `ADROBOT_` variable
on purpose: it is not something an operator tunes per deployment, and every variable is one
more name that has to be spelled correctly under `extra="forbid"`.

**A failure darkens the column and never the editor.** Three states come out of here, and
`CampaignStats` names all three: today's numbers, the last numbers that could be read with
a note saying so, and no numbers with the same note. Serving the stale reading is the point
of keeping one — a figure dated ten minutes ago is worth more than a blank cell, and it is
dated, so nobody has to guess whether it is current.

**Nothing here logs.** The transport has already written the failed request — method, path,
status, redacted body — against this request's correlation id, so a swallowed exception
costs an operator nothing, and this ring stays clear of structlog for the reason
`ports/system.py` gives about the correlation id itself.

**One reading per campaign, keyed by nothing else, and compared against the day it holds.**
Midnight in the tracker's zone therefore invalidates by construction: a reading whose day is
not today's is a miss, and a failure that finds one falls through to "no numbers" rather
than showing yesterday's clicks under today's heading.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from adrobot.application.dto import CampaignStats, OfferClicks, StreamClicks
from adrobot.application.errors import UpstreamError

if TYPE_CHECKING:
    from datetime import date, datetime

    from adrobot.application.ports.keitaro import KeitaroReportsPort
    from adrobot.application.ports.system import Clock
    from adrobot.application.time_zone import TrackerTimeZone
    from adrobot.domain.ids import KeitaroCampaignId

STATS_TTL: Final = timedelta(seconds=45)
"""How long one reading is believed. The whole of the staleness of a healthy column."""

MAX_HELD_CAMPAIGNS: Final = 512
"""The ceiling on the readings kept. A cache this small needs no eviction policy, but an
unbounded dictionary on a process that runs for weeks is a leak however slow it fills; past
this, the lot is dropped and whoever is looking pays for one more round of reports."""

UNAVAILABLE: Final = "today's numbers could not be read from the tracker"
"""What the column says when the report builder will not answer.

This service's own sentence and never the tracker's. An `UpstreamError` renders as the
detail of a 502 — which environment variable holds the refused key, what the tracker said —
and `api/errors.py` withholds exactly that outside dev. A 200 carrying it in a field would
be the same disclosure through a door left open, so the reason a client reads is fixed and
the reason an operator needs is in the log line the transport already wrote."""


class StatsReader:
    """One campaign's clicks today, from the tracker or from the last time it answered."""

    def __init__(
        self,
        reports: KeitaroReportsPort,
        clock: Clock,
        *,
        zone: TrackerTimeZone,
        ttl: timedelta = STATS_TTL,
    ) -> None:
        self._reports = reports
        self._clock = clock
        # The tracker's zone, which is what decides where today begins — and what every
        # report is then asked for, so that the day this works out and the day the tracker
        # reports on cannot come apart. Whatever it resolves to is a name `zoneinfo` has
        # already accepted, so `ZoneInfo` below cannot fail on a request.
        self._zone = zone
        self._ttl = ttl
        self._held: dict[KeitaroCampaignId, CampaignStats] = {}

    async def read(self, campaign_id: KeitaroCampaignId) -> CampaignStats:
        """Answer with today's numbers, refreshing them when what is held has gone cold.

        There is deliberately no lock around the refresh. Filling this cache has no side
        effect — unlike the reference resolver's, which creates a group — so two requests
        arriving together cost one duplicated pair of reports and nothing else, while a lock
        held across two HTTP calls would make one slow campaign stop every other campaign's
        column for as long as the tracker took.
        """
        timezone = await self._zone.resolve()
        at = self._clock.now()
        day = at.astimezone(ZoneInfo(timezone)).date()
        held = self._held.get(campaign_id)
        if held is not None and held.day != day:
            held = None
        fresh = self._fresh(held, at)
        if fresh is not None:
            return fresh
        try:
            reading = await self._from_tracker(campaign_id, day, at, timezone=timezone)
        except UpstreamError:
            return self._degraded(held, day, timezone)
        self._keep(campaign_id, reading)
        return reading

    async def _from_tracker(
        self, campaign_id: KeitaroCampaignId, day: date, at: datetime, *, timezone: str
    ) -> CampaignStats:
        """Build one reading out of the two reports that make the whole screen.

        Sequential and not gathered: the adapter discovers which dialect of `/report/build`
        this build of the tracker speaks on its first call, so a second report launched
        alongside the first would rediscover it — and a failure of either would leave the
        other in flight with nobody to read its result.
        """
        by_stream = await self._reports.clicks_by_stream(campaign_id, day, timezone=timezone)
        by_offer = await self._reports.clicks_by_offer(campaign_id, day, timezone=timezone)
        return CampaignStats(
            day=day,
            timezone=timezone,
            read_at=at,
            # Ordered by id, so that two reads of one screen answer identically whatever
            # order the report came back in. Nothing reads the order: the table draws each
            # row where `display_order` already put it.
            streams=tuple(
                StreamClicks(keitaro_stream_id=stream_id, clicks=clicks)
                for stream_id, clicks in sorted(by_stream.items())
            ),
            offers=tuple(
                OfferClicks(offer_id=offer_id, clicks=stats.clicks, conversions=stats.conversions)
                for offer_id, stats in sorted(by_offer.items())
            ),
        )

    def _degraded(self, held: CampaignStats | None, day: date, timezone: str) -> CampaignStats:
        """Answer without the tracker: the last reading if there is one, nothing if not.

        The failed attempt is not kept. What is held stays the newest reading that was ever
        real, so a tracker that is down for an hour goes on serving one dated figure rather
        than forgetting it, and the first successful read replaces it outright.
        """
        if held is None:
            return CampaignStats(day=day, timezone=timezone, unavailable_reason=UNAVAILABLE)
        return replace(held, unavailable_reason=UNAVAILABLE)

    def _fresh(self, held: CampaignStats | None, at: datetime) -> CampaignStats | None:
        """Return what is held while it is still inside the window, and `None` otherwise."""
        if held is None or held.read_at is None:
            return None
        return held if at - held.read_at < self._ttl else None

    def _keep(self, campaign_id: KeitaroCampaignId, reading: CampaignStats) -> None:
        """Hold one reading, emptying the cache wholesale rather than letting it grow."""
        if len(self._held) >= MAX_HELD_CAMPAIGNS and campaign_id not in self._held:
            self._held.clear()
        self._held[campaign_id] = reading
