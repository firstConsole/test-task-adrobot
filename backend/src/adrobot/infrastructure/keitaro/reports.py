"""The report builder, which is the one endpoint of this API the schema is wrong about.

`Report.rows` is typed as an array of strings and is an array of objects. Two shipping
clients send `grouping` and `metrics` where the schema says `dimensions` and `measures`.
Neither can be settled without a live tracker, and both are settled here rather than by a
statistics screen that comes back empty with no explanation:

*   the request goes out in the schema's dialect, and exactly one rejection is answered by
    asking again in the clients' — after which the process remembers which one worked;
*   the answer is read as `{rows: [...]}` or as a bare array, whichever arrives.

A row that cannot be read is skipped rather than raised over. This is a column of numbers
beside a working editor, and stage 8.2 darkens it when the tracker will not answer at all;
losing the whole screen because one row of a report is odd would be the wrong trade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, override

from adrobot.application.errors import UpstreamRejectedError
from adrobot.application.ports.keitaro import KeitaroReportsPort
from adrobot.domain.ids import KeitaroStreamId, OfferId
from adrobot.domain.offer import OfferStats
from adrobot.infrastructure.keitaro.mapping import report_body, to_report_rows

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from adrobot.domain.ids import KeitaroCampaignId
    from adrobot.infrastructure.keitaro.transport import KeitaroTransport

REPORT_PATH: Final = "/report/build"

STREAM_DIMENSION: Final = "stream_id"
OFFER_DIMENSION: Final = "offer_id"

CLICKS: Final = "clicks"
CONVERSIONS: Final = "conversions"

# The two key names the published schema and the shipping clients disagree about, and the
# whole of the difference between the dialects.
_CLIENT_NAMES: Final = {"dimensions": "grouping", "measures": "metrics"}


class HttpKeitaroReports(KeitaroReportsPort):
    """`POST /report/build`, twice: once grouped by flow and once by offer."""

    def __init__(self, transport: KeitaroTransport, *, timezone: str) -> None:
        self._transport = transport
        # The IANA name rather than a `tzinfo`, because that is what goes in the body. A
        # report asked for in the wrong zone answers with somebody else's midnight, which
        # is the difference between "clicks today" and a number nobody can account for.
        self._timezone = timezone
        self._as_clients = False

    @override
    async def clicks_by_stream(
        self, campaign_id: KeitaroCampaignId, day: date
    ) -> Mapping[KeitaroStreamId, int]:
        """Return the clicks each flow of one campaign took on one day, in the tracker's zone."""
        rows = await self._build(campaign_id, day, dimension=STREAM_DIMENSION, measures=(CLICKS,))
        return {
            KeitaroStreamId(key): _whole(row.get(CLICKS))
            for row in rows
            if (key := _identifier(row, STREAM_DIMENSION)) is not None
        }

    @override
    async def clicks_by_offer(
        self, campaign_id: KeitaroCampaignId, day: date
    ) -> Mapping[OfferId, OfferStats]:
        """Return the same day grouped by offer — the editor's Stats column, in one call."""
        rows = await self._build(
            campaign_id, day, dimension=OFFER_DIMENSION, measures=(CLICKS, CONVERSIONS)
        )
        return {
            OfferId(key): OfferStats(
                clicks=_whole(row.get(CLICKS)), conversions=_whole(row.get(CONVERSIONS))
            )
            for row in rows
            if (key := _identifier(row, OFFER_DIMENSION)) is not None
        }

    async def _build(
        self,
        campaign_id: KeitaroCampaignId,
        day: date,
        *,
        dimension: str,
        measures: tuple[str, ...],
    ) -> tuple[Mapping[str, Any], ...]:
        """Ask for one report, in whichever dialect this build of the tracker understands."""
        body = report_body(
            dimension=dimension,
            measures=measures,
            campaign_id=campaign_id,
            day=day,
            timezone=self._timezone,
        )
        try:
            answered = await self._transport.query(
                REPORT_PATH, json=_spoken(body, self._as_clients)
            )
        except UpstreamRejectedError:
            if self._as_clients:
                raise
            # A rejected body, once, from the one endpoint whose request shape is in
            # dispute. Asking again in the other dialect is cheaper than a statistics
            # screen that is empty for a reason nobody can see. Remembered only after it
            # works, so a body rejected for some other reason does not switch the dialect.
            answered = await self._transport.query(REPORT_PATH, json=_spoken(body, as_clients=True))
            self._as_clients = True
        return to_report_rows(answered.json())


def _spoken(body: dict[str, Any], as_clients: bool) -> dict[str, Any]:  # noqa: FBT001 — one private call site, and the name is at it
    """Rename the two keys the shipping clients spell differently, or leave the body alone."""
    if not as_clients:
        return body
    return {_CLIENT_NAMES.get(key, key): value for key, value in body.items()}


def _identifier(row: Mapping[str, Any], dimension: str) -> int | None:
    """Read the row's own id, skipping a row that has none to be keyed by."""
    value = row.get(dimension)
    if isinstance(value, bool) or not isinstance(value, int | str):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _whole(value: object) -> int:
    """Read one measure. Keitaro has been known to send its numbers as strings."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0
