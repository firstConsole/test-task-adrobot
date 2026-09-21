"""The Stats column: what one campaign did today, by flow and by offer.

The whole screen is two calls to the tracker, and that is the point of the scenario rather
than an incidental fact about it. A column that asked per row would be one request per
offer against the most fragile endpoint Keitaro has, from a screen that redraws itself on
every edit; PLAN-FRONTEND §8 says the frontend builds one map per page for the same reason
this reads one report per grouping.

**Sequential and not gathered.** Two reports could go out together, and running them one
after the other costs a round trip — which is paid back twice over: the adapter discovers
which dialect of `/report/build` this build of the tracker speaks on its first call, so a
second call launched alongside the first would rediscover it, and a failure of either would
leave the other in flight with nobody to read its result.

**The campaign is read, and the transaction is closed, before the tracker is called.** The
mirror is consulted for one thing only — the tracker's own id for this campaign, and the
404 for a campaign that is not here — and holding a connection open across two HTTP calls
empties the pool the moment reports are slow, which is exactly when a screen asks again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from adrobot.application.dto import CampaignStats, OfferClicks, StreamClicks

if TYPE_CHECKING:
    from adrobot.application.ports.keitaro import KeitaroReportsPort
    from adrobot.application.ports.persistence import UnitOfWork
    from adrobot.application.ports.system import Clock
    from adrobot.domain.ids import CampaignId


class GetCampaignStats:
    """One campaign's clicks today, grouped by flow and by offer, in two reports."""

    def __init__(
        self,
        *,
        reports: KeitaroReportsPort,
        uow: UnitOfWork,
        clock: Clock,
        timezone: str,
    ) -> None:
        self._reports = reports
        self._uow = uow
        self._clock = clock
        # The tracker's zone by name, which is what decides where today begins. Validated
        # against the tz database at boot, so `ZoneInfo` below cannot fail on a request.
        self._timezone = timezone

    async def __call__(self, campaign_id: CampaignId) -> CampaignStats:
        """Read today's numbers, or raise `CampaignNotFoundError` for a campaign not here.

        "Today" is the tracker's, worked out from this service's clock in the tracker's
        zone. Asking for this machine's date would answer with yesterday's clicks for as
        many hours as the two zones are apart — a column that is wrong without looking
        wrong, which is the failure this whole indirection exists to prevent.
        """
        async with self._uow.begin() as transaction:
            campaign = await transaction.campaigns.get(campaign_id)
        at = self._clock.now()
        day = at.astimezone(ZoneInfo(self._timezone)).date()
        tracked = campaign.keitaro_campaign_id
        by_stream = await self._reports.clicks_by_stream(tracked, day)
        by_offer = await self._reports.clicks_by_offer(tracked, day)
        return CampaignStats(
            day=day,
            timezone=self._timezone,
            read_at=at,
            streams=tuple(
                StreamClicks(keitaro_stream_id=stream_id, clicks=clicks)
                for stream_id, clicks in sorted(by_stream.items())
            ),
            offers=tuple(
                OfferClicks(offer_id=offer_id, clicks=stats.clicks, conversions=stats.conversions)
                for offer_id, stats in sorted(by_offer.items())
            ),
        )
