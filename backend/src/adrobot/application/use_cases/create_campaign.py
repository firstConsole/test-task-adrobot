"""Part 1, in two scenarios: the create, and the second half of it as a button of its own.

The whole of part 1 is three writes to somebody else's system with no transaction around
them — a campaign, then two flows — and the question the design answers is what the person
who pressed the button is left holding when the second or the third one fails.

**Nothing is rolled back.** Deleting a campaign that exists in the tracker, on the word of a
request that is already failing, is the one outcome that cannot be undone from the screen.
What happens instead is that the local row is written the moment the campaign exists, marked
`needs_attention`, carrying the country and the offer that were asked for — so the campaign
is visible, named, and finishable.

**`RepairCampaign` is that finish, and `CreateCampaign` ends by calling it.** Not because
the create needs a second scenario, but because a compensation path that only runs after a
failure is a path that is broken half the time nobody is looking: this way the button that
finishes a half-built campaign is the same code that builds every campaign, and it cannot
rot without every create going red.

The one window neither of them closes is between `POST /campaigns` answering and our own
row being written: a process that dies in it leaves a campaign in the tracker that this
service has never heard of. Closing it would need a row written before the campaign exists,
and the tracker's id — the natural key of that row — is what the create is asking for.
Importing (6.4) is how such a campaign is adopted afterwards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adrobot.application.dto import CampaignView
from adrobot.application.errors import CampaignNotRepairableError, UpstreamError
from adrobot.application.ports.persistence import CampaignSetup
from adrobot.domain.campaign import CampaignBlueprint, CampaignSetupStatus, flows_for
from adrobot.domain.values import CountryCode

if TYPE_CHECKING:
    from adrobot.application.dto import CreateCampaignCommand
    from adrobot.application.ports.keitaro import KeitaroAdminPort
    from adrobot.application.ports.persistence import MirroredCampaign, UnitOfWork
    from adrobot.application.ports.system import AliasFactory, Clock
    from adrobot.application.reference import ReferenceResolver
    from adrobot.domain.ids import CampaignId, OfferId
    from adrobot.domain.stream import Stream, StreamSpec


class RepairCampaign:
    """Give a campaign the flows it should have, and mirror whatever the tracker then holds.

    Idempotent because it reads before it writes: a flow the tracker already has is left
    alone, so pressing the button twice — or pressing it on a campaign whose first flow
    landed and whose second did not — adds what is missing and nothing else.
    """

    def __init__(self, *, admin: KeitaroAdminPort, uow: UnitOfWork, clock: Clock) -> None:
        self._admin = admin
        self._uow = uow
        self._clock = clock

    async def __call__(self, campaign_id: CampaignId) -> CampaignView:
        """Finish this campaign's setup, or say what the tracker would not let us finish."""
        async with self._uow.begin() as transaction:
            campaign = await transaction.campaigns.get(campaign_id)
        if campaign.setup_status is CampaignSetupStatus.READY:
            # Nothing to finish, and deliberately no read of the tracker to check: a flow
            # deleted in Keitaro afterwards is somebody's edit, not our unfinished work, and
            # rebuilding it from under them is not what this button says.
            return CampaignView.of(campaign)
        country, offer_id = _requested(campaign)
        try:
            streams, missing = await self._build(campaign, country=country, offer_id=offer_id)
        except UpstreamError as refusal:
            return CampaignView.of(campaign, setup_failure=str(refusal))
        return await self._mirror(campaign, streams, missing=missing)

    async def _build(
        self, campaign: MirroredCampaign, *, country: CountryCode, offer_id: OfferId
    ) -> tuple[tuple[Stream, ...], tuple[StreamSpec, ...]]:
        """Create the flows the tracker does not have, then read back what it does have."""
        tracker_id = campaign.keitaro_campaign_id
        wanted = flows_for(tracker_id, country=country, offer_id=offer_id)
        for spec in _missing_from(await self._admin.list_campaign_streams(tracker_id), wanted):
            await self._create(spec)
        # Read back rather than keep the echoes of the creates: the mirror wants each offer
        # row's own id and `created_at`, which is what orders the rounding remainder for
        # rows that came from the tracker, and only a read states them.
        written = await self._admin.list_campaign_streams(tracker_id)
        return written, _missing_from(written, wanted)

    async def _create(self, spec: StreamSpec) -> None:
        """Create one flow, and repair it in place if the create dropped part of it.

        The published schema declares no `action_payload` on any flow request and marks no
        field of one as required, so whether a create keeps Flow 1's redirect and Flow 1's
        country filter is a question about the build rather than about the API. This asks
        the tracker instead of assuming: if what came back is missing either, the same
        specification is written again with a `PUT`, which is the two-step shape the plan
        holds in reserve — taken only when it is needed.
        """
        created = await self._admin.create_stream(spec)
        if _dropped_something(spec, created):
            await self._admin.update_stream(created.id, spec)

    async def _mirror(
        self,
        campaign: MirroredCampaign,
        streams: tuple[Stream, ...],
        *,
        missing: tuple[StreamSpec, ...],
    ) -> CampaignView:
        """Write the flows into the mirror and record how far the setup got."""
        at = self._clock.now()
        async with self._uow.begin() as transaction:
            await transaction.streams.upsert_campaign_streams(
                campaign_id=campaign.id, streams=streams, at=at
            )
            await transaction.campaigns.set_setup_status(
                campaign.id,
                CampaignSetupStatus.NEEDS_ATTENTION if missing else CampaignSetupStatus.READY,
            )
            # Read back inside the same transaction: the row handed in is the one from
            # before the two writes above, and answering with it would report the status
            # this call has just changed.
            written = await transaction.campaigns.get(campaign.id)
        return CampaignView.of(written, setup_failure=_unfinished(missing))


class CreateCampaign:
    """Part 1: three fields in, a campaign with two flows in the tracker, a link out."""

    def __init__(
        self,
        *,
        admin: KeitaroAdminPort,
        uow: UnitOfWork,
        references: ReferenceResolver,
        aliases: AliasFactory,
        clock: Clock,
    ) -> None:
        self._admin = admin
        self._uow = uow
        self._references = references
        self._aliases = aliases
        self._clock = clock
        # Built here rather than injected, out of the three ports this scenario already
        # holds. A create whose second half could be replaced by a test double would stop
        # proving the thing the arrangement exists to prove: that the repair button and the
        # create run the same code.
        self._repair = RepairCampaign(admin=admin, uow=uow, clock=clock)

    async def __call__(self, command: CreateCampaignCommand) -> CampaignView:
        """Create the campaign, record what it was asked to be, and build its flows."""
        references = await self._references.resolve()
        campaign = await self._admin.create_campaign(
            CampaignBlueprint(
                name=command.name,
                alias=self._aliases.new(),
                group_id=references.group_id,
                traffic_source_id=references.traffic_source_id,
                domain_id=None if references.domain is None else references.domain.id,
            )
        )
        at = self._clock.now()
        async with self._uow.begin() as transaction:
            # `needs_attention` from the first moment, and the three requested values with
            # it. The campaign exists in the tracker by now; everything below can fail, and
            # this row is what makes that failure a state somebody can act on.
            created = await transaction.campaigns.add(
                campaign,
                setup=CampaignSetup(
                    status=CampaignSetupStatus.NEEDS_ATTENTION,
                    public_domain=None if references.domain is None else references.domain.name,
                    requested_country=command.country,
                    requested_offer_id=command.offer_id,
                ),
            )
            # This campaign was read from the tracker's own answer a moment ago. `add` has
            # no way to date that, and `note_fetched` is the one method that does.
            await transaction.campaigns.note_fetched(created.id, campaign, at=at)
        return await self._repair(created.id)


def _requested(campaign: MirroredCampaign) -> tuple[CountryCode, OfferId]:
    """Read back what part 1 was asked to build, refusing a row that does not say.

    The country is validated again on the way out, against the same list it passed on the
    way in. It costs a set lookup and it means a row edited by hand cannot make this service
    build a campaign aimed at a country that does not exist.
    """
    if campaign.requested_country is None or campaign.requested_offer_id is None:
        raise CampaignNotRepairableError(campaign.id)
    return CountryCode(campaign.requested_country), campaign.requested_offer_id


def _missing_from(
    held: tuple[Stream, ...], wanted: tuple[StreamSpec, ...]
) -> tuple[StreamSpec, ...]:
    """Return the specifications the tracker has no flow for, in the order they are built.

    By position, which is what a `position` campaign dispatches on, and by name as well:
    `Stream.position` is optional in the published schema, and against a build that answers
    without it every repair would otherwise add another pair of flows.
    """
    return tuple(
        spec
        for spec in wanted
        if not any(
            (spec.position is not None and stream.position == spec.position)
            or stream.name == spec.name
            for stream in held
        )
    )


def _dropped_something(spec: StreamSpec, echoed: Stream) -> bool:
    """Whether the create gave back a flow missing something it was told to have.

    Presence, never equality: the tracker assigns a filter its own id and is free to hand a
    payload back in its own case, so comparing the two filter tuples would report a
    difference on every create and turn every create into a create plus an update.
    """
    if spec.action_payload is not None and not echoed.action_payload:
        return True
    return bool(spec.filters) and not echoed.filters


def _unfinished(missing: tuple[StreamSpec, ...]) -> str | None:
    """Name the flows that are still not there, for the person who pressed the button.

    Reached when every call answered and the flow is still absent — the tracker accepted a
    create and has nothing to show for it. The campaign stays `needs_attention`, which is
    a state the repair button can be pressed on again; a verdict that also demanded the geo
    filter would be one no number of presses could satisfy.
    """
    if not missing:
        return None
    return f"the tracker still has no {' and no '.join(spec.name for spec in missing)}"
