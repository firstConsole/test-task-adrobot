"""Part 1, end to end over fakes: a campaign, its two flows, and what is left of both on a
half-failure.

The assertions about the flows are literal — `Flow 1`, `Flow 2`, `redirect`, `landings`,
`country accept ["MX"]` — because that is what a reviewer compares against the reference
campaign, field by field, with the tracker open in another tab.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

import pytest

from adrobot.application.dto import CreateCampaignCommand
from adrobot.application.errors import (
    CampaignNotRepairableError,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from adrobot.application.ports.persistence import CampaignSetup
from adrobot.application.reference import ReferenceResolver
from adrobot.application.use_cases.create_campaign import CreateCampaign, RepairCampaign
from adrobot.domain.campaign import (
    FIRST_FLOW,
    GEO_REDIRECT_URL,
    SECOND_FLOW,
    Campaign,
    CampaignSetupStatus,
    ReferenceData,
)
from adrobot.domain.ids import KeitaroCampaignId, OfferId
from adrobot.domain.stream import Stream, StreamSchema, StreamSpec
from adrobot.domain.values import CampaignName, CountryCode
from tests.fake_persistence import FakeUnitOfWork, WatchesTheDatabase
from tests.fakes import DEFAULT_REFERENCE, FakeAliasFactory, FakeClock, FakeKeitaroAdmin

OFFER = OfferId(3749)
COMMAND = CreateCampaignCommand(
    name=CampaignName("Summer MX"), country=CountryCode("MX"), offer_id=OFFER
)


@dataclass(frozen=True, slots=True, kw_only=True)
class World:
    """One wiring of part 1, with every fake it was built from still reachable."""

    create: CreateCampaign
    repair: RepairCampaign
    admin: FakeKeitaroAdmin
    uow: FakeUnitOfWork
    clock: FakeClock


def world(
    *, reference: ReferenceData = DEFAULT_REFERENCE, admin: FakeKeitaroAdmin | None = None
) -> World:
    tracker = admin if admin is not None else FakeKeitaroAdmin(reference=reference)
    clock = FakeClock()
    uow = FakeUnitOfWork(clock)
    return World(
        create=CreateCampaign(
            admin=tracker,
            uow=uow,
            references=ReferenceResolver(tracker, clock),
            aliases=FakeAliasFactory(),
            clock=clock,
        ),
        repair=RepairCampaign(admin=tracker, uow=uow, clock=clock),
        admin=tracker,
        uow=uow,
        clock=clock,
    )


def flow(admin: FakeKeitaroAdmin, name: str) -> Stream:
    return next(stream for stream in admin.streams.values() if stream.name == name)


async def test_the_campaign_carries_the_name_the_alias_and_the_three_references() -> None:
    stage = world()

    view = await stage.create(COMMAND)

    created = stage.admin.campaigns[KeitaroCampaignId(view.campaign.keitaro_campaign_id)]
    assert created.name == "Summer MX"
    assert created.alias == "kt-alias-1"
    assert created.group_id == 7
    assert created.traffic_source_id == 2
    assert created.cost_type == "CPC"
    assert created.cookies_ttl == 24
    # The domain is the one field the tracker takes and never gives back, so the blueprint
    # is the only place it can be checked at all.
    assert stage.admin.blueprints[0].domain_id == 4


async def test_the_first_flow_catches_the_country_and_sends_it_to_the_task_s_address() -> None:
    stage = world()

    await stage.create(COMMAND)

    first = flow(stage.admin, FIRST_FLOW)
    assert first.position == 1
    assert first.schema is StreamSchema.REDIRECT
    assert first.action_payload == GEO_REDIRECT_URL
    assert [(rule.name, rule.mode, rule.payload) for rule in first.filters] == [
        ("country", "accept", ("MX",))
    ]


async def test_the_second_flow_rotates_the_one_offer_at_the_whole_share() -> None:
    stage = world()

    await stage.create(COMMAND)

    second = flow(stage.admin, SECOND_FLOW)
    assert second.position == 2
    assert second.schema is StreamSchema.LANDINGS
    assert {row.offer_id: row.share for row in second.offers} == {OFFER: 100}
    assert [row.state for row in second.offers] == ["active"]


async def test_the_row_remembers_what_part_one_was_asked_for() -> None:
    stage = world()

    view = await stage.create(COMMAND)

    assert view.campaign.setup_status is CampaignSetupStatus.READY
    assert view.campaign.requested_country == "MX"
    assert view.campaign.requested_offer_id == OFFER
    assert view.campaign.public_domain == "track.example"
    assert view.campaign.synced_at == stage.clock.at
    assert view.public_url == "https://track.example/kt-alias-1"
    assert view.setup_failure is None


async def test_the_mirror_holds_both_flows_and_the_offer_row() -> None:
    stage = world()

    view = await stage.create(COMMAND)

    async with stage.uow.begin() as transaction:
        mirrored = await transaction.streams.views_for(view.campaign.id)
    assert [held.stream.name for held in mirrored] == [FIRST_FLOW, SECOND_FLOW]
    assert mirrored[0].mirror_rows == ()
    assert {row.offer_id: row.share for row in mirrored[1].mirror_rows} == {OFFER: 100}


async def test_a_tracker_with_no_domain_creates_the_campaign_and_no_link() -> None:
    stage = world(reference=ReferenceData(campaign_groups=DEFAULT_REFERENCE.campaign_groups))

    view = await stage.create(COMMAND)

    assert view.campaign.setup_status is CampaignSetupStatus.READY
    assert view.campaign.public_domain is None
    assert view.public_url is None
    assert stage.admin.blueprints[0].domain_id is None


async def test_the_database_is_never_held_open_while_the_tracker_is_called() -> None:
    stage = world()
    watched = WatchesTheDatabase(stage.uow)
    guarded = world(admin=watched)

    await guarded.create(COMMAND)

    # Two blocks around the campaign write and the mirror, plus the repair's own read of the
    # row — and none of them open while any of the six tracker calls was in flight.
    assert guarded.uow.blocks == 3
    assert not guarded.uow.open


class DropsWhatItWasNotAsked(FakeKeitaroAdmin):
    """A build that silently discards a flow's payload and filters on the create.

    No request schema in the published specification declares `action_payload`, and none of
    them marks a field required — so this is a real shape a tracker could have, and the
    difference it makes is a campaign whose geo targeting quietly does not exist.
    """

    @override
    async def create_stream(self, spec: StreamSpec) -> Stream:
        return await super().create_stream(
            StreamSpec(
                campaign_id=spec.campaign_id,
                name=spec.name,
                type=spec.type,
                schema=spec.schema,
                action_type=spec.action_type,
                position=spec.position,
                offers=spec.offers,
            )
        )


async def test_a_create_that_drops_the_redirect_and_the_filter_is_written_again() -> None:
    stage = world(admin=DropsWhatItWasNotAsked())

    await stage.create(COMMAND)

    assert stage.admin.calls.count("update_stream") == 1
    first = flow(stage.admin, FIRST_FLOW)
    assert first.action_payload == GEO_REDIRECT_URL
    assert [rule.payload for rule in first.filters] == [("MX",)]


class RefusesTheSecondFlow(FakeKeitaroAdmin):
    """A tracker that takes Flow 1 and refuses Flow 2, which is the half-failure to survive."""

    def __init__(self) -> None:
        super().__init__()
        self.refusing = True
        self.refusal = "offer 3749 is not available"

    @override
    async def create_stream(self, spec: StreamSpec) -> Stream:
        if spec.name == SECOND_FLOW and self.refusing:
            raise UpstreamRejectedError(self.refusal, status=406)
        return await super().create_stream(spec)


async def test_a_flow_that_was_refused_leaves_a_campaign_somebody_can_finish() -> None:
    stage = world(admin=RefusesTheSecondFlow())

    view = await stage.create(COMMAND)

    assert view.campaign.setup_status is CampaignSetupStatus.NEEDS_ATTENTION
    assert view.setup_failure is not None
    assert "offer 3749" in view.setup_failure
    # The campaign itself is in the tracker, and the row knows which one it is.
    assert view.campaign.keitaro_campaign_id in stage.admin.campaigns
    assert view.public_url == "https://track.example/kt-alias-1"


async def test_repairing_adds_the_missing_flow_and_leaves_the_one_that_landed() -> None:
    tracker = RefusesTheSecondFlow()
    stage = world(admin=tracker)
    view = await stage.create(COMMAND)

    tracker.refusing = False
    finished = await stage.repair(view.campaign.id)

    assert finished.campaign.setup_status is CampaignSetupStatus.READY
    assert finished.setup_failure is None
    assert sorted(stream.name for stream in stage.admin.streams.values()) == [
        FIRST_FLOW,
        SECOND_FLOW,
    ]
    async with stage.uow.begin() as transaction:
        mirrored = await transaction.streams.views_for(view.campaign.id)
    assert [held.stream.name for held in mirrored] == [FIRST_FLOW, SECOND_FLOW]


async def test_repairing_twice_creates_nothing_the_second_time() -> None:
    tracker = RefusesTheSecondFlow()
    stage = world(admin=tracker)
    view = await stage.create(COMMAND)
    tracker.refusing = False
    await stage.repair(view.campaign.id)

    stage.admin.calls.clear()
    await stage.repair(view.campaign.id)

    # Ready and finished: the button is idempotent to the point of not asking the tracker.
    assert stage.admin.calls == []


async def test_a_tracker_that_is_down_does_not_lose_the_campaign() -> None:
    stage = world()
    stage.admin.fail_on("create_stream", UpstreamUnavailableError("timed out"))

    view = await stage.create(COMMAND)

    assert view.campaign.setup_status is CampaignSetupStatus.NEEDS_ATTENTION
    assert view.campaign.keitaro_campaign_id in stage.admin.campaigns
    async with stage.uow.begin() as transaction:
        stored = await transaction.campaigns.get(view.campaign.id)
    assert stored.keitaro_campaign_id == view.campaign.keitaro_campaign_id


async def test_a_campaign_nobody_here_created_cannot_be_repaired() -> None:
    stage = world()
    async with stage.uow.begin() as transaction:
        adopted = await transaction.campaigns.add(
            Campaign(
                id=KeitaroCampaignId(93212), alias="abcdef", name="Somebody else's", state="active"
            ),
            setup=CampaignSetup(status=CampaignSetupStatus.NEEDS_ATTENTION),
        )

    with pytest.raises(CampaignNotRepairableError):
        await stage.repair(adopted.id)


class ForgetsTheFlow(FakeKeitaroAdmin):
    """A tracker that accepts a create, echoes the flow back and does not keep it.

    The failure nothing raises on: every call answered, every answer looked right, and the
    campaign has no flows. Only reading the campaign back finds it.
    """

    @override
    async def create_stream(self, spec: StreamSpec) -> Stream:
        created = await super().create_stream(spec)
        del self.streams[created.id]
        return created


async def test_a_flow_the_tracker_accepted_and_did_not_keep_leaves_the_setup_unfinished() -> None:
    stage = world(admin=ForgetsTheFlow())

    view = await stage.create(COMMAND)

    assert view.campaign.setup_status is CampaignSetupStatus.NEEDS_ATTENTION
    assert view.setup_failure == f"the tracker still has no {FIRST_FLOW} and no {SECOND_FLOW}"
