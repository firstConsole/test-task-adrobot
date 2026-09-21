"""That the composition root wires the real adapters, and opens nothing while doing it.

The second half is what makes this testable at all: `create_async_engine` connects lazily
and `httpx.AsyncClient` opens no socket until a request, so entering and leaving this block
against a tracker URL that does not resolve and a database host that does not exist is
exactly what a process does before its first request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adrobot.application.reference import ReferenceResolver
from adrobot.application.statistics import StatsReader
from adrobot.composition import build_ports
from adrobot.infrastructure.db.uow import SqlAlchemyUnitOfWork
from adrobot.infrastructure.keitaro.admin import HttpKeitaroAdmin
from adrobot.infrastructure.keitaro.reports import HttpKeitaroReports
from adrobot.infrastructure.system import SecretsAliasFactory, SystemClock

if TYPE_CHECKING:
    from adrobot.settings import Settings


async def test_it_builds_the_adapters_the_service_actually_talks_through(
    settings: Settings,
) -> None:
    async with build_ports(settings) as ports:
        assert isinstance(ports.admin, HttpKeitaroAdmin)
        assert isinstance(ports.reports, HttpKeitaroReports)
        assert isinstance(ports.references, ReferenceResolver)
        assert isinstance(ports.statistics, StatsReader)
        assert isinstance(ports.aliases, SecretsAliasFactory)
        assert isinstance(ports.clock, SystemClock)


async def test_the_reference_cache_is_built_on_the_same_tracker_every_request_uses(
    settings: Settings,
) -> None:
    # Private on purpose and read anyway: a resolver wired to a second admin would still
    # answer, and its five-minute cache would be a five-minute cache of nothing.
    async with build_ports(settings) as ports:
        assert ports.references._admin is ports.admin


async def test_the_statistics_reader_is_built_on_the_same_report_builder_and_the_same_zone(
    settings: Settings,
) -> None:
    # Same argument as above, and one more: the zone the reader works today out in has to be
    # the zone the adapter puts in the report body, or the window either side of midnight
    # asks for one day and labels it another.
    async with build_ports(settings) as ports:
        assert ports.statistics._reports is ports.reports
        assert ports.statistics._timezone == settings.keitaro_timezone


async def test_the_unit_of_work_is_asked_for_per_request_and_not_shared(
    settings: Settings,
) -> None:
    async with build_ports(settings) as ports:
        async with ports.unit_of_work() as first:
            pass
        async with ports.unit_of_work() as second:
            pass

    assert isinstance(first, SqlAlchemyUnitOfWork)
    # Two requests, two sessions. One shared unit of work would put two requests' reads in
    # one identity map and their writes in one transaction.
    assert first is not second
