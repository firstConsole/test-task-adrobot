"""Which zone the tracker keeps its own clock in, asked once and then never again.

Keitaro serialises every timestamp without an offset, in a zone it does not put on the wire
(PLAN-00 §5.14). Everything that has to know where a day begins therefore depends on a
single string, and this is where that string is decided.

**The tracker is asked, and `ADROBOT_KEITARO_TIMEZONE` is what stands when it will not
answer.** `GET /settings` is in no part of the published schema, so it may simply not exist
on a given build; a refusal is not a failure here, it is the configured value going
unconfirmed. That is also why an answer this service cannot resolve against its own tz
database is discarded rather than raised over: the fallback is a value an operator declared
on purpose, and a tracker replying `MSK` or `+03:00` is not a reason to stop serving
statistics.

**Asked lazily, on the first screen that needs it, and never from the boot path.** A network
call at start-up would make a tracker that is down at the wrong moment into a service that
will not start — and `/readyz` deliberately does not reach Keitaro for exactly that reason.

**Asked once, whatever the outcome.** A zone is a fact about a deployment and not about a
minute, so there is no TTL and no retry: a tracker that was unreachable when the first
report was built leaves this process on the configured value until it is restarted, which
is the value an operator wrote down and the one every report was already being asked for
in. Re-asking on every screen would put an undocumented request in front of each one to
re-confirm a constant.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from adrobot.application.errors import UpstreamError

if TYPE_CHECKING:
    from adrobot.application.ports.keitaro import KeitaroAdminPort


class TrackerTimeZone:
    """The tracker's zone by IANA name, resolved from it once and remembered."""

    def __init__(self, admin: KeitaroAdminPort, *, configured: str) -> None:
        self._admin = admin
        # Already resolved against this machine's tz database by `Settings`, so this is the
        # answer that cannot fail and the reason nothing below has to raise.
        self._configured = configured
        self._resolved: str | None = None
        # Built here although there is no running loop yet, which asyncio has allowed since
        # 3.10: a lock no longer binds itself to a loop until it is first awaited.
        self._lock = asyncio.Lock()

    async def resolve(self) -> str:
        """Answer with the zone every report is asked for and every day is worked out in.

        Locked, unlike the statistics cache it is read from — and for the opposite reason to
        the one that leaves *that* unlocked. This is asked once for the life of the process
        and answered from memory forever after, so the lock can only ever delay the very
        first screen, while without it every request in flight during that first resolve
        would put its own call to an undocumented path against the tracker.
        """
        if self._resolved is not None:
            return self._resolved
        async with self._lock:
            # Asked again inside the lock: whoever held it has just answered the question,
            # and without this re-read that whole queue would go on to ask the tracker.
            if self._resolved is None:
                self._resolved = await self._ask()
            return self._resolved

    async def _ask(self) -> str:
        """Read the tracker's zone, falling back to the configured one on anything at all."""
        try:
            answered = await self._admin.get_time_zone()
        except UpstreamError:
            # Already logged by the transport, with this request's correlation id. A path
            # the schema does not declare failing to answer is the expected case, not an
            # incident.
            return self._configured
        return self._configured if not _resolvable(answered) else str(answered)


def _resolvable(name: str | None) -> bool:
    """Whether this machine can turn the tracker's answer into a zone at all.

    A name that `zoneinfo` cannot resolve would fail later, inside the request that computes
    a date — so it is refused here, where the configured value is still there to be used.
    """
    if not name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True
