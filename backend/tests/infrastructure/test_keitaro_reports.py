"""The statistics adapter against a tracker whose report endpoint the schema describes wrongly.

Two of these are about the ambiguity that could not be settled without a live tracker — the
dialect of the request and the shape of the answer — and the rest are about a column of
numbers behaving like a column of numbers rather than like an exception.
"""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx

from adrobot.application.errors import UpstreamRejectedError, UpstreamUnavailableError
from adrobot.domain.ids import KeitaroCampaignId
from adrobot.domain.offer import OfferStats
from adrobot.infrastructure.keitaro.reports import HttpKeitaroReports
from adrobot.infrastructure.keitaro.transport import KeitaroTransport, build_keitaro_client
from tests.helpers import VALID_ENVIRONMENT

if TYPE_CHECKING:
    from respx.models import Route

    from adrobot.settings import Settings

BASE = VALID_ENVIRONMENT["ADROBOT_KEITARO_BASE_URL"]
CAMPAIGN_ID = KeitaroCampaignId(93212)
DAY = date(2026, 9, 20)


def _sent(route: Route, index: int = 0) -> dict[str, Any]:
    body = json.loads(route.calls[index].request.content)
    assert isinstance(body, dict)
    return body


@pytest.fixture
def reports(settings: Settings) -> HttpKeitaroReports:
    return HttpKeitaroReports(
        KeitaroTransport(build_keitaro_client(settings), backoff=0.0), timezone="Europe/Madrid"
    )


async def test_a_report_is_asked_for_in_the_schema_s_own_dialect(
    reports: HttpKeitaroReports,
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/report/build").mock(
            return_value=httpx.Response(200, json={"rows": [], "total": 0})
        )

        await reports.clicks_by_stream(CAMPAIGN_ID, DAY)

    body = _sent(route)
    assert body["dimensions"] == ["stream_id"]
    assert body["measures"] == ["clicks"]
    assert body["range"] == {
        "timezone": "Europe/Madrid",
        "from": "2026-09-20",
        "to": "2026-09-20",
    }, "the day is the caller's, and the tracker's today is not always the same date"
    assert body["filters"] == [
        {"name": "campaign_id", "operator": "EQUALS", "expression": 93212}
    ], "a report filter, not the {name, mode, payload} a flow filter uses"


async def test_a_build_that_speaks_the_other_dialect_is_asked_again_in_it(
    reports: HttpKeitaroReports,
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/report/build").mock(
            side_effect=[
                httpx.Response(400, json={"error": "unknown parameter dimensions"}),
                httpx.Response(200, json={"rows": [{"stream_id": 564221, "clicks": 7}]}),
            ]
        )

        clicks = await reports.clicks_by_stream(CAMPAIGN_ID, DAY)

    assert _sent(route, 1)["grouping"] == ["stream_id"], "two shipping clients spell it this way"
    assert _sent(route, 1)["metrics"] == ["clicks"]
    assert clicks == {564221: 7}


async def test_the_dialect_that_worked_is_the_one_used_next_time(
    reports: HttpKeitaroReports,
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/report/build").mock(
            side_effect=[
                httpx.Response(406, json={"error": "no"}),
                httpx.Response(200, json={"rows": []}),
                httpx.Response(200, json={"rows": []}),
            ]
        )

        await reports.clicks_by_stream(CAMPAIGN_ID, DAY)
        await reports.clicks_by_offer(CAMPAIGN_ID, DAY)

    assert route.call_count == 3, "the second report does not repeat the first one's mistake"
    assert "grouping" in _sent(route, 2)


async def test_a_body_rejected_in_both_dialects_is_reported(reports: HttpKeitaroReports) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/report/build").mock(return_value=httpx.Response(400, json={"e": "no"}))

        with pytest.raises(UpstreamRejectedError):
            await reports.clicks_by_stream(CAMPAIGN_ID, DAY)

    assert route.call_count == 2, "asked twice at most, and never a third time"


async def test_once_the_dialect_is_settled_a_refusal_is_just_a_refusal(
    reports: HttpKeitaroReports,
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/report/build").mock(
            side_effect=[
                httpx.Response(400, json={"error": "unknown parameter dimensions"}),
                httpx.Response(200, json={"rows": []}),
                httpx.Response(400, json={"error": "unknown measure"}),
            ]
        )

        await reports.clicks_by_stream(CAMPAIGN_ID, DAY)
        with pytest.raises(UpstreamRejectedError, match="unknown measure"):
            await reports.clicks_by_offer(CAMPAIGN_ID, DAY)

    assert route.call_count == 3, (
        "the dialect is no longer in question, so the second report is not asked twice"
    )


async def test_an_unreachable_report_is_not_dressed_up_as_zero_clicks(
    reports: HttpKeitaroReports,
) -> None:
    # Degrading belongs to the use case at 8.2, which knows whether an empty column or a
    # banner is the right answer. An adapter that returned {} would have decided for it.
    async with respx.mock(base_url=BASE) as mock:
        mock.post("/report/build").mock(return_value=httpx.Response(503))

        with pytest.raises(UpstreamUnavailableError):
            await reports.clicks_by_stream(CAMPAIGN_ID, DAY)


async def test_an_answer_that_is_a_bare_array_is_read_too(reports: HttpKeitaroReports) -> None:
    async with respx.mock(base_url=BASE) as mock:
        mock.post("/report/build").mock(
            return_value=httpx.Response(200, json=[{"stream_id": 564221, "clicks": 3}])
        )

        assert await reports.clicks_by_stream(CAMPAIGN_ID, DAY) == {564221: 3}


async def test_clicks_and_conversions_come_back_per_offer(reports: HttpKeitaroReports) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/report/build").mock(
            return_value=httpx.Response(
                200,
                json={
                    "rows": [
                        {"offer_id": 3749, "clicks": 12, "conversions": 2},
                        {"offer_id": 11112, "clicks": 0, "conversions": 0},
                    ]
                },
            )
        )

        stats = await reports.clicks_by_offer(CAMPAIGN_ID, DAY)

    assert _sent(route)["measures"] == ["clicks", "conversions"], (
        "two measures, because every extra name is one a particular build might reject"
    )
    assert stats == {
        3749: OfferStats(clicks=12, conversions=2),
        11112: OfferStats(clicks=0, conversions=0),
    }


async def test_numbers_that_arrived_as_strings_are_still_numbers(
    reports: HttpKeitaroReports,
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        mock.post("/report/build").mock(
            return_value=httpx.Response(
                200, json={"rows": [{"stream_id": "564221", "clicks": "7"}]}
            )
        )

        assert await reports.clicks_by_stream(CAMPAIGN_ID, DAY) == {564221: 7}


@pytest.mark.parametrize(
    "row",
    [
        {"clicks": 7},
        {"stream_id": None, "clicks": 7},
        {"stream_id": "Flow 2", "clicks": 7},
        {"stream_id": True, "clicks": 7},
    ],
)
async def test_a_row_with_nothing_to_key_it_by_is_skipped(
    reports: HttpKeitaroReports, row: dict[str, Any]
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        mock.post("/report/build").mock(return_value=httpx.Response(200, json={"rows": [row]}))

        assert await reports.clicks_by_stream(CAMPAIGN_ID, DAY) == {}


@pytest.mark.parametrize("measure", [None, "lots", True, {"value": 7}])
async def test_a_measure_that_is_not_a_number_reads_as_none_of_them(
    reports: HttpKeitaroReports, measure: object
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        mock.post("/report/build").mock(
            return_value=httpx.Response(200, json={"rows": [{"stream_id": 1, "clicks": measure}]})
        )

        assert await reports.clicks_by_stream(CAMPAIGN_ID, DAY) == {1: 0}
