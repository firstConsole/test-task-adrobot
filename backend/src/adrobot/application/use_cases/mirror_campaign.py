"""Two ways a campaign in the tracker becomes a campaign on this screen: adopting, refreshing.

The task calls part 2 an editor of an *existing* campaign, and the video edits campaign
93212, which was built by hand. `ImportCampaign` is what makes that possible at all; without
it the editor could only open what part 1 had made, and the one campaign a reviewer is most
likely to try is the one from the video. `SyncCampaign` is the same read a second time —
FETCH STREAMS FROM KT on the editor's toolbar — and the two live together because they are
one job with one difference: whether the row has to be created first.

What is adopted is deliberately thin. The row records that the campaign is ours to edit and
nothing about what it should look like: `requested_country` and `requested_offer_id` stay
empty, which is what makes `RepairCampaign` refuse it — somebody else's campaign is not
half-built, it is simply not ours to finish. `public_domain` stays empty for a harder
reason: `Campaign` carries no `domain_id`, so the domain a campaign is served on cannot be
read back, and an invented link is worse than an absent one.

The flows are mirrored **exactly as the tracker holds them** — shares included, and never
normalised. A hand-built flow whose offers sum to 50% is a real flow taking real traffic,
and the editor's whole purpose is to show what is there before it changes anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adrobot.application.dto import CampaignView
from adrobot.application.errors import CampaignAlreadyImportedError
from adrobot.application.ports.persistence import CampaignSetup
from adrobot.domain.campaign import CampaignSetupStatus

if TYPE_CHECKING:
    from datetime import datetime

    from adrobot.application.ports.keitaro import KeitaroAdminPort
    from adrobot.application.ports.persistence import (
        MirroredCampaign,
        Transaction,
        UnitOfWork,
    )
    from adrobot.application.ports.system import Clock
    from adrobot.domain.campaign import Campaign
    from adrobot.domain.ids import CampaignId, KeitaroCampaignId
    from adrobot.domain.stream import Stream


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
        campaign, streams = await _fetch(self._admin, keitaro_campaign_id)
        at = self._clock.now()
        async with self._uow.begin() as transaction:
            # `ready`: there is nothing here for part 1 to finish, and the status is what
            # says so. The insert is also the race's last word — two imports that both got
            # past the read above meet at the unique key, and the loser is told by `add`.
            row = await transaction.campaigns.add(
                campaign, setup=CampaignSetup(status=CampaignSetupStatus.READY)
            )
            written = await _write_mirror(
                transaction, campaign_id=row.id, campaign=campaign, streams=streams, at=at
            )
        return CampaignView.of(written)


class SyncCampaign:
    """Read a campaign and its flows again, and leave the mirror holding what came back.

    This is FETCH STREAMS FROM KT, and it is the answer to every "the tracker and this
    screen disagree" — including the one a push causes by being made in another tab.

    A flow or an offer row the tracker no longer returns is **tombstoned, never deleted**:
    it stays on the screen, grey, at 0%, with BRING BACK live, which is the behaviour the
    reference tool has and the most characteristic thing about it. That is also why an
    answer with no flows at all is believed rather than second-guessed — the campaign may
    genuinely have been emptied in Keitaro, and believing it is reversible in a way that
    inventing flows would not be: the next fetch that sees them again un-tombstones every
    row it returns.

    A live draft survives this untouched. It is staged in tables of its own, and the
    `base_snapshot_hash` it was opened with is what notices the mirror having moved
    underneath it — at the push, where the answer can be a 409 somebody can act on rather
    than a silent rebase.
    """

    def __init__(self, *, admin: KeitaroAdminPort, uow: UnitOfWork, clock: Clock) -> None:
        self._admin = admin
        self._uow = uow
        self._clock = clock

    async def __call__(self, campaign_id: CampaignId) -> CampaignView:
        """Refresh one campaign from the tracker, or raise if we have no such campaign."""
        async with self._uow.begin() as transaction:
            # Read and closed: what follows is two network calls, and a transaction held
            # across them is a connection held for as long as the tracker feels like taking.
            row = await transaction.campaigns.get(campaign_id)
        campaign, streams = await _fetch(self._admin, row.keitaro_campaign_id)
        at = self._clock.now()
        async with self._uow.begin() as transaction:
            written = await _write_mirror(
                transaction, campaign_id=campaign_id, campaign=campaign, streams=streams, at=at
            )
        return CampaignView.of(written)


async def _fetch(
    admin: KeitaroAdminPort, keitaro_campaign_id: KeitaroCampaignId
) -> tuple[Campaign, tuple[Stream, ...]]:
    """Read one campaign and its flows, one call after the other.

    Sequentially, although they are independent: a `TaskGroup` would raise an
    `ExceptionGroup`, and which failure came out is exactly what decides whether the answer
    is 404, 502 or a refused admin key. Shared by both scenarios above so that adopting a
    campaign and refreshing one cannot drift apart in what they consider a campaign to be.
    """
    campaign = await admin.get_campaign(keitaro_campaign_id)
    return campaign, await admin.list_campaign_streams(keitaro_campaign_id)


async def _write_mirror(
    transaction: Transaction,
    *,
    campaign_id: CampaignId,
    campaign: Campaign,
    streams: tuple[Stream, ...],
    at: datetime,
) -> MirroredCampaign:
    """Leave the mirror holding this campaign and exactly these flows, and read it back.

    Takes the transaction rather than opening one, so that the caller's insert and this
    write are one transaction and not two — and so that the shared part cannot quietly
    become a second place that decides how long a transaction is.
    """
    await transaction.campaigns.note_fetched(campaign_id, campaign, at=at)
    await transaction.streams.upsert_campaign_streams(
        campaign_id=campaign_id, streams=streams, at=at
    )
    # Read back inside the same transaction: the row from before these writes would report
    # a `synced_at` this call has just moved.
    return await transaction.campaigns.get(campaign_id)
