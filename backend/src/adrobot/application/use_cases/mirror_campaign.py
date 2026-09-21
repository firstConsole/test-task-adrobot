"""Adopting a campaign this service did not create — part 2's entry point.

The task calls part 2 an editor of an *existing* campaign, and the video edits campaign
93212, which was built by hand. Without this the editor could only open what part 1 had
made, and the one campaign a reviewer is most likely to try is the one from the video.

What is adopted is deliberately thin. The row records that the campaign is ours to edit and
nothing about what it should look like: `requested_country` and `requested_offer_id` stay
empty, which is what makes `RepairCampaign` refuse it — somebody else's campaign is not
half-built, it is simply not ours to finish. `public_domain` stays empty for a harder
reason: `Campaign` carries no `domain_id`, so the domain a campaign is served on cannot be
read back, and an invented link is worse than an absent one.

The flows are read in the same breath and mirrored **exactly as the tracker holds them** —
shares included, and never normalised. A hand-built flow whose offers sum to 50% is a real
flow taking real traffic, and the editor's whole purpose is to show what is there before it
changes anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adrobot.application.dto import CampaignView
from adrobot.application.errors import CampaignAlreadyImportedError
from adrobot.application.ports.persistence import CampaignSetup
from adrobot.domain.campaign import CampaignSetupStatus

if TYPE_CHECKING:
    from adrobot.application.ports.keitaro import KeitaroAdminPort
    from adrobot.application.ports.persistence import UnitOfWork
    from adrobot.application.ports.system import Clock
    from adrobot.domain.ids import KeitaroCampaignId


class ImportCampaign:
    """Open a campaign that already exists in the tracker, flows and all."""

    def __init__(self, *, admin: KeitaroAdminPort, uow: UnitOfWork, clock: Clock) -> None:
        self._admin = admin
        self._uow = uow
        self._clock = clock

    async def __call__(self, keitaro_campaign_id: KeitaroCampaignId) -> CampaignView:
        """Mirror one tracker campaign and its flows, refusing one that is already here."""
        async with self._uow.begin() as transaction:
            adopted = await transaction.campaigns.by_keitaro_id(keitaro_campaign_id)
        if adopted is not None:
            # Refused before the tracker is touched, and naming the copy that exists: a
            # second row would give one flow two editors, each with its own draft.
            raise CampaignAlreadyImportedError(keitaro_campaign_id, campaign_id=adopted.id)
        campaign = await self._admin.get_campaign(keitaro_campaign_id)
        streams = await self._admin.list_campaign_streams(keitaro_campaign_id)
        at = self._clock.now()
        async with self._uow.begin() as transaction:
            # `ready`: there is nothing here for part 1 to finish, and the status is what
            # says so. The insert is also the race's last word — two imports that both got
            # past the read above meet at the unique key, and the loser is told by `add`.
            row = await transaction.campaigns.add(
                campaign, setup=CampaignSetup(status=CampaignSetupStatus.READY)
            )
            await transaction.campaigns.note_fetched(row.id, campaign, at=at)
            await transaction.streams.upsert_campaign_streams(
                campaign_id=row.id, streams=streams, at=at
            )
            written = await transaction.campaigns.get(row.id)
        return CampaignView.of(written)
