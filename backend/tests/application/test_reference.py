"""That a campaign is built on the group, source and domain a person would have picked.

The numbers a reviewer checks are the shares, and none of them are here — but every one of
the choices below is visible in the tracker's own interface the moment a campaign appears
in it, under a heading somebody either recognises or does not.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import override

import pytest

from adrobot.application.errors import UpstreamRejectedError
from adrobot.application.reference import (
    CAMPAIGN_GROUP_NAME,
    REFERENCE_TTL,
    ReferenceResolver,
)
from adrobot.domain.campaign import Group, ReferenceData, TrackerDomain, TrafficSource
from tests.fakes import DEFAULT_REFERENCE, FakeClock, FakeKeitaroAdmin


def resolver(
    reference: ReferenceData = DEFAULT_REFERENCE,
) -> tuple[ReferenceResolver, FakeKeitaroAdmin, FakeClock]:
    admin = FakeKeitaroAdmin(reference=reference)
    clock = FakeClock()
    return ReferenceResolver(admin, clock), admin, clock


async def test_it_picks_the_group_the_source_and_the_domain_the_tracker_lists_first() -> None:
    resolve, _, _ = resolver()

    references = await resolve.resolve()

    assert references.group_id == 7
    assert references.traffic_source_id == 2
    assert references.domain == TrackerDomain(id=4, name="track.example", state="active")


async def test_it_skips_a_deleted_source_and_a_deleted_domain() -> None:
    resolve, _, _ = resolver(
        ReferenceData(
            campaign_groups=(Group(id=1, name="Campaigns"),),
            traffic_sources=(
                TrafficSource(id=8, name="Old Facebook", state="deleted"),
                TrafficSource(id=9, name="Facebook", state="active"),
            ),
            domains=(
                TrackerDomain(id=2, name="expired.example", state="deleted"),
                TrackerDomain(id=3, name="track.example", state="active"),
            ),
        )
    )

    references = await resolve.resolve()

    assert references.traffic_source_id == 9
    assert references.domain is not None
    assert references.domain.name == "track.example"


async def test_a_row_the_tracker_lists_without_a_state_still_counts() -> None:
    # `Source.state` is an undocumented string in the published schema, so a build that
    # answers without it must not read as a tracker whose every source has been deleted.
    resolve, _, _ = resolver(
        ReferenceData(
            campaign_groups=(Group(id=1, name="Campaigns"),),
            traffic_sources=(TrafficSource(id=5, name="Facebook"),),
            domains=(TrackerDomain(id=6, name="track.example"),),
        )
    )

    references = await resolve.resolve()

    assert references.traffic_source_id == 5
    assert references.domain is not None
    assert references.domain.id == 6


async def test_a_tracker_with_no_source_and_no_domain_still_yields_a_campaign() -> None:
    # Both are optional on the create. The campaign is what the button promised; the link
    # is what the screen then cannot show, which is a message and not a failed request.
    resolve, _, _ = resolver(ReferenceData(campaign_groups=(Group(id=1, name="Campaigns"),)))

    references = await resolve.resolve()

    assert references.group_id == 1
    assert references.traffic_source_id is None
    assert references.domain is None


async def test_it_prefers_its_own_group_over_the_one_the_tracker_lists_first() -> None:
    resolve, _, _ = resolver(
        ReferenceData(
            campaign_groups=(
                Group(id=1, name="Archive"),
                Group(id=2, name=CAMPAIGN_GROUP_NAME),
            )
        )
    )

    assert (await resolve.resolve()).group_id == 2


async def test_it_creates_its_group_when_the_tracker_has_none() -> None:
    resolve, admin, _ = resolver(ReferenceData())

    references = await resolve.resolve()

    assert admin.calls == ["list_reference_data", "create_campaign_group"]
    assert admin.reference.campaign_groups[0].name == CAMPAIGN_GROUP_NAME
    assert references.group_id == admin.reference.campaign_groups[0].id


async def test_a_second_resolve_asks_the_tracker_nothing() -> None:
    resolve, admin, _ = resolver()

    await resolve.resolve()
    await resolve.resolve()

    assert admin.calls == ["list_reference_data"]


async def test_the_cache_is_dropped_once_it_is_older_than_the_ttl() -> None:
    resolve, admin, clock = resolver()
    await resolve.resolve()

    clock.advance(REFERENCE_TTL - timedelta(seconds=1))
    await resolve.resolve()
    assert admin.calls == ["list_reference_data"]

    clock.advance(timedelta(seconds=1))
    await resolve.resolve()
    assert admin.calls == ["list_reference_data", "list_reference_data"]


async def test_forgetting_makes_the_next_resolve_read_the_tracker_again() -> None:
    resolve, admin, _ = resolver()
    await resolve.resolve()

    resolve.forget()
    await resolve.resolve()

    assert admin.calls == ["list_reference_data", "list_reference_data"]


class SuspendingAdmin(FakeKeitaroAdmin):
    """A tracker whose read actually yields, so that two callers can interleave on it.

    Without the `sleep(0)` the fake answers without ever suspending, the first caller runs
    to completion before the second one starts, and the test below would pass against a
    resolver that has no lock at all.
    """

    @override
    async def list_reference_data(self) -> ReferenceData:
        await asyncio.sleep(0)
        return await super().list_reference_data()


async def test_two_creates_arriving_together_create_one_group() -> None:
    # The defect this prevents outlives the request that caused it: two groups named "AD
    # Robot" in a tracker somebody else has to tidy up, and campaigns split between them.
    admin = SuspendingAdmin(reference=ReferenceData())
    resolve = ReferenceResolver(admin, FakeClock())

    first, second = await asyncio.gather(resolve.resolve(), resolve.resolve())

    assert admin.calls == ["list_reference_data", "create_campaign_group"]
    assert first == second


async def test_a_refused_read_is_not_cached_as_an_answer() -> None:
    resolve, admin, _ = resolver()
    admin.fail_on("list_reference_data", UpstreamRejectedError("no", status=406))

    with pytest.raises(UpstreamRejectedError):
        await resolve.resolve()

    admin.failures.clear()
    assert (await resolve.resolve()).group_id == 7
