"""The facts a use case cannot be handed as an argument, behind ports like everything else.

What is here is what makes a scenario's answer depend on something other than its inputs —
the wall clock so far, the random alphabet of an alias at 6.3. Both are one line of the
standard library, and behind a port for the same reason the tracker is: a test that cannot
say what time it is has to sleep, and a test that cannot say which alias will be generated
has to read the answer it is checking.

Neither method is `async`. Every other port in this package is a call that leaves the
process and every method on it is awaited; these two reach no further than this machine,
and an `async def now()` would only make `await` the price of asking what time it is.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


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
