"""Where the rings are wired together, and the only module allowed to see all of them.

Everything else in this service names an abstraction: a use case asks for `KeitaroAdminPort`
and gets whatever this module built. That is what `.importlinter`'s second contract says in
so many words — a use case that imported *this* module would reach infrastructure through
it, and the contract fails on the indirect edge as well as the direct one.

Two lifetimes, and telling them apart is the whole job.

**Per process** is everything below: one HTTP client for the tracker, one connection pool,
one reference cache. They are opened by the block this module hands out and closed when it
leaves, and neither resource is opened *here* — `keitaro_transport` and `database` each own
their own, so this module composes two blocks and knows nothing about sockets.

**Per request** is the unit of work, and it is carried as a factory rather than as an object
for exactly that reason: a session belongs to one request, and `AppPorts` outlives every
request it serves. The factory is also what keeps SQLAlchemy out of `api/` — the HTTP layer
calls it and gets a `UnitOfWork`, never a sessionmaker.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from adrobot.application.ports.keitaro import KeitaroAdminPort, KeitaroReportsPort
from adrobot.application.ports.persistence import UnitOfWork
from adrobot.application.ports.system import AliasFactory, Clock
from adrobot.application.reference import ReferenceResolver
from adrobot.infrastructure.db.engine import database
from adrobot.infrastructure.db.uow import unit_of_work
from adrobot.infrastructure.keitaro.admin import HttpKeitaroAdmin
from adrobot.infrastructure.keitaro.reports import HttpKeitaroReports
from adrobot.infrastructure.keitaro.transport import keitaro_transport
from adrobot.infrastructure.system import SecretsAliasFactory, SystemClock

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from adrobot.settings import Settings

UnitOfWorkFactory = Callable[[], AbstractAsyncContextManager[UnitOfWork]]
"""One request's database work, asked for when the request arrives and closed when it ends."""


@dataclass(frozen=True, slots=True, kw_only=True)
class AppPorts:
    """Everything a request is served from, built once for the life of the process.

    Frozen, because this is configuration that has finished being decided: a request that
    could swap a port would be a request deciding what the next request talks to.
    """

    admin: KeitaroAdminPort
    reports: KeitaroReportsPort
    references: ReferenceResolver
    aliases: AliasFactory
    clock: Clock
    unit_of_work: UnitOfWorkFactory


@asynccontextmanager
async def build_ports(settings: Settings) -> AsyncIterator[AppPorts]:
    """Open this process's resources, wire the ports over them, and close them afterwards.

    The two blocks are entered together and left in reverse, which is what a single `async
    with` over both already does — and what a pair of nested `try/finally` would have to be
    written carefully to do.
    """
    async with keitaro_transport(settings) as transport, database(settings) as sessions:
        clock = SystemClock()
        # The zone is the tracker's own, and both adapters need it in a different shape: the
        # admin parses naive timestamps with a `tzinfo`, the report builder sends the IANA
        # name in the request body. Resolved once here rather than twice down there.
        admin = HttpKeitaroAdmin(transport, zone=ZoneInfo(settings.keitaro_timezone))
        yield AppPorts(
            admin=admin,
            reports=HttpKeitaroReports(transport, timezone=settings.keitaro_timezone),
            # One resolver for the process, which is what makes its five-minute cache worth
            # having and what makes the campaign group be created once rather than per
            # request.
            references=ReferenceResolver(admin, clock),
            aliases=SecretsAliasFactory(),
            clock=clock,
            unit_of_work=partial(unit_of_work, sessions),
        )
