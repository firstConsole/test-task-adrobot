"""The Stats column: what one campaign did today, by flow and by offer.

Thin, and thin on purpose. Everything about *reading* the numbers — how often the tracker is
really asked, which day is today in its zone, what the column says when the report builder
will not answer — belongs to `application/statistics.py`, which lives for the whole process
and therefore has somewhere to keep a reading. What is left here is the part that is a
scenario: this campaign, and whether it is one of ours at all.

**The campaign is read, and the transaction is closed, before the tracker is called.** The
mirror is consulted for one thing only — the tracker's own id for this campaign, and the 404
for a campaign that is not here — and holding a connection open across two HTTP calls
empties the pool the moment reports are slow, which is exactly when a screen asks again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adrobot.application.dto import CampaignStats
    from adrobot.application.ports.persistence import UnitOfWork
    from adrobot.application.statistics import StatsReader
    from adrobot.domain.ids import CampaignId


class GetCampaignStats:
    """One campaign's clicks today, grouped by flow and by offer."""

    def __init__(self, *, stats: StatsReader, uow: UnitOfWork) -> None:
        self._stats = stats
        self._uow = uow

    async def __call__(self, campaign_id: CampaignId) -> CampaignStats:
        """Read today's numbers, or raise `CampaignNotFoundError` for a campaign not here.

        The tracker is asked about *its* campaign id and never about ours: the id in the URL
        is this service's, and a report filtered on it would answer about somebody else's
        campaign or, far more often, about none at all — a column of zeroes that looks
        exactly like a campaign nobody has clicked on yet.
        """
        async with self._uow.begin() as transaction:
            campaign = await transaction.campaigns.get(campaign_id)
        return await self._stats.read(campaign.keitaro_campaign_id)
