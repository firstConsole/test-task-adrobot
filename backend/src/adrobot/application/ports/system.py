"""The facts a use case cannot be handed as an argument, behind ports like everything else.

What is here is what makes a scenario's answer depend on something other than its inputs —
the wall clock, the random alphabet of an alias, the id of the request being served. Each is
one or two lines of the standard library, and behind a port for the same reason the tracker
is: a test that cannot say what time it is has to sleep, one that cannot say which alias
will be generated has to read the answer it is checking, and one that cannot say which
correlation id a push will record cannot assert on the audit row at all.

No method here is `async`. Every other port in this package is a call that leaves the
process and every method on it is awaited; these reach no further than this machine, and an
`async def now()` would only make `await` the price of asking what time it is.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from adrobot.domain.values import CampaignAlias


class Clock(ABC):
    """The current moment, as a dependency.

    One implementation reads the system clock; the other is a test saying that five minutes
    and one second have passed, which is how the reference cache below is checked without
    a suite that takes five minutes to run.
    """

    @abstractmethod
    def now(self) -> datetime:
        """Return the current moment, timezone-aware and in UTC.

        Aware, always: every timestamp column of this service is `timestamptz`, and a naive
        value reaching one is stored as though the process had been running in UTC — which
        it is, until somebody runs it anywhere else. The tracker's own zone is a separate
        matter and is applied where its naive timestamps are read, not here.
        """


class AliasFactory(ABC):
    """The path segment a campaign's public link ends in, generated rather than chosen.

    Behind a port because it is the one part of a create whose result cannot be predicted,
    and a scenario test that cannot predict it has to read the alias out of the answer it is
    checking. The production implementation draws a fixed number of characters from a safe
    alphabet; filtering something like `token_urlsafe(8)` down to `[a-z0-9]` would be
    shorter than six characters about a third of the time, which is a 500 on the very first
    button rather than a rare one.

    Collisions are not handled here and not retried anywhere: the tracker refuses a
    duplicate alias, that refusal becomes an `UpstreamRejectedError`, and a create is the
    one call this service never repeats on its own.
    """

    @abstractmethod
    def new(self) -> CampaignAlias:
        """Generate one alias. Returns the validated type, so an unusable one cannot leave."""


class CorrelationIds(ABC):
    """The id tying one request's log lines, its problem body and its audit row together.

    Behind a port rather than read from `adrobot.logging` directly, which would be one
    import and would also drag structlog into a ring whose whole claim is that it depends on
    nothing. The value itself is a `ContextVar` the HTTP middleware binds; what a use case
    needs to know is only that asking for it always yields one.
    """

    @abstractmethod
    def current(self) -> str:
        """Return the id of the work being done, minting one where nothing has bound it.

        Never `None`, unlike the accessor underneath: `push_attempts.correlation_id` is NOT
        NULL, and a CLI push is work worth correlating even though no request began it.
        """
