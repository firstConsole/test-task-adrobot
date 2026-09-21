"""One application's worth of fakes, wired the way `composition.py` wires the real thing.

`tests/fakes.py` is the tracker and `tests/fake_persistence.py` is the database; this is the
`AppPorts` they add up to, and the factory `create_app` takes in place of `build_ports`.

Everything here is shared for the life of one test, which is the point: the campaign a
request creates is in the same dictionary the next request reads, exactly as two requests
share one pool and one tracker in production.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from adrobot.application.reference import ReferenceResolver
from adrobot.application.statistics import StatsReader
from adrobot.application.time_zone import TrackerTimeZone
from adrobot.composition import AppPorts
from tests.fake_persistence import FakeUnitOfWork
from tests.fakes import (
    FakeAliasFactory,
    FakeClock,
    FakeCorrelationIds,
    FakeKeitaroAdmin,
    FakeKeitaroReports,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from contextlib import AbstractAsyncContextManager

    from adrobot.application.ports.persistence import UnitOfWork
    from adrobot.settings import Settings


@dataclass(frozen=True, slots=True, kw_only=True)
class FakeWorld:
    """The ports an application is served from, and every fake they were built out of."""

    ports: AppPorts
    admin: FakeKeitaroAdmin
    reports: FakeKeitaroReports
    uow: FakeUnitOfWork
    clock: FakeClock
    aliases: FakeAliasFactory
    correlation: FakeCorrelationIds


def fake_world(
    *,
    admin: FakeKeitaroAdmin | None = None,
    reports: FakeKeitaroReports | None = None,
    timezone: str = "UTC",
) -> FakeWorld:
    """Compose one application's ports out of fakes, as `build_ports` composes the real ones.

    `timezone` is the *configured* zone — what stands when the tracker names none, which is
    what `FakeKeitaroAdmin` does unless a test says otherwise.
    """
    tracker = admin if admin is not None else FakeKeitaroAdmin()
    statistics = reports if reports is not None else FakeKeitaroReports()
    clock = FakeClock()
    zone = TrackerTimeZone(tracker, configured=timezone)
    uow = FakeUnitOfWork(clock)
    aliases = FakeAliasFactory()
    correlation = FakeCorrelationIds()
    return FakeWorld(
        ports=AppPorts(
            admin=tracker,
            reports=statistics,
            references=ReferenceResolver(tracker, clock),
            statistics=StatsReader(statistics, clock, zone=zone),
            zone=zone,
            aliases=aliases,
            clock=clock,
            correlation=correlation,
            unit_of_work=partial(_one_unit_of_work, uow),
        ),
        admin=tracker,
        reports=statistics,
        uow=uow,
        clock=clock,
        aliases=aliases,
        correlation=correlation,
    )


def fake_ports_factory(
    ports: AppPorts | None = None,
) -> Callable[[Settings], AbstractAsyncContextManager[AppPorts]]:
    """Hand `create_app` a factory that yields these ports and ignores the settings.

    Builds a world of its own when given none, for the tests that need an application and
    nothing behind it — the docs policy, the access log, the correlation id.
    """
    held = ports if ports is not None else fake_world().ports

    @asynccontextmanager
    async def factory(_: Settings) -> AsyncIterator[AppPorts]:
        yield held

    return factory


@asynccontextmanager
async def _one_unit_of_work(uow: UnitOfWork) -> AsyncIterator[UnitOfWork]:
    """Hand every request the same in-memory database, which is what a pool does too."""
    yield uow
