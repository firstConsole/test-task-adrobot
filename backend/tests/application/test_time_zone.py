"""Where a day begins, and what happens when the tracker will not say.

Every number on the statistics screen hangs off one string. Get it wrong and nothing looks
wrong: the report comes back, the column fills, and for as many hours as two zones are apart
it is yesterday's traffic under today's heading.
"""

from __future__ import annotations

import asyncio
from typing import override

from adrobot.application.errors import UpstreamNotFoundError, UpstreamUnavailableError
from adrobot.application.time_zone import TrackerTimeZone
from tests.fakes import FakeKeitaroAdmin

CONFIGURED = "UTC"


def resolver(
    *, tracker_says: str | None = None, configured: str = CONFIGURED
) -> tuple[TrackerTimeZone, FakeKeitaroAdmin]:
    admin = FakeKeitaroAdmin()
    admin.time_zone = tracker_says
    return TrackerTimeZone(admin, configured=configured), admin


async def test_the_tracker_is_believed_over_the_variable_that_only_guessed_at_it() -> None:
    zone, _ = resolver(tracker_says="Australia/Sydney", configured="UTC")

    assert await zone.resolve() == "Australia/Sydney"


async def test_a_build_that_names_no_zone_leaves_the_configured_one_standing() -> None:
    zone, _ = resolver(tracker_says=None, configured="Europe/Madrid")

    assert await zone.resolve() == "Europe/Madrid"


async def test_a_build_without_the_path_is_not_a_failure() -> None:
    zone, admin = resolver(configured="Europe/Madrid")
    # `GET /settings` is in no part of the published schema. A 404 from it means this
    # tracker does not have it, which is the expected case and not an incident.
    admin.fail_on("get_time_zone", UpstreamNotFoundError("no such path", status=404))

    assert await zone.resolve() == "Europe/Madrid"


async def test_a_tracker_that_cannot_be_reached_leaves_the_configured_zone_standing() -> None:
    zone, admin = resolver(configured="Europe/Madrid")
    admin.fail_on("get_time_zone", UpstreamUnavailableError("connect timed out"))

    assert await zone.resolve() == "Europe/Madrid"


async def test_a_zone_this_machine_cannot_resolve_is_discarded_rather_than_believed() -> None:
    # Abbreviations and offsets are both in circulation and neither is an IANA name. Taking
    # one would move the failure into the request that computes a date, where the configured
    # value is no longer in reach.
    zone, _ = resolver(tracker_says="MSK", configured="Europe/Madrid")

    assert await zone.resolve() == "Europe/Madrid"


async def test_an_empty_string_is_not_a_zone() -> None:
    zone, _ = resolver(tracker_says="", configured="Europe/Madrid")

    assert await zone.resolve() == "Europe/Madrid"


async def test_the_tracker_is_asked_once_and_then_never_again() -> None:
    zone, admin = resolver(tracker_says="Australia/Sydney")

    assert [await zone.resolve() for _ in range(5)] == ["Australia/Sydney"] * 5
    assert admin.calls == ["get_time_zone"]


async def test_a_failure_is_not_retried_either_because_a_zone_is_not_a_minute() -> None:
    zone, admin = resolver(configured="Europe/Madrid")
    admin.fail_on("get_time_zone", UpstreamUnavailableError("connect timed out"))

    await zone.resolve()
    await zone.resolve()

    # A screen redrawing every few seconds would otherwise put an undocumented request in
    # front of each redraw, to re-confirm a constant.
    assert admin.calls == ["get_time_zone"]


async def test_requests_arriving_together_on_a_cold_process_ask_the_tracker_once() -> None:
    admin = _SlowAdmin("Australia/Sydney")
    zone = TrackerTimeZone(admin, configured=CONFIGURED)

    answers = await asyncio.gather(*(zone.resolve() for _ in range(4)))

    assert answers == ["Australia/Sydney"] * 4
    assert admin.calls == ["get_time_zone"]


class _SlowAdmin(FakeKeitaroAdmin):
    """A tracker that yields to the loop before answering, so a race can actually happen.

    Without the suspension every coroutine would run to completion before the next one
    started, and the lock under test would never be contended.
    """

    def __init__(self, zone: str) -> None:
        super().__init__()
        self.time_zone = zone

    @override
    async def get_time_zone(self) -> str | None:
        self._called("get_time_zone")
        await asyncio.sleep(0)
        return self.time_zone
