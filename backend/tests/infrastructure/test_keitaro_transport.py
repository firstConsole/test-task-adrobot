"""What the transport does when the tracker is slow, wrong, or somewhere else entirely.

The retry policy is the part of this project that can spend somebody's money twice, so the
tests that matter here are the ones that count calls. The rest is about the key: it is
carried in a header the whole world logs by accident, and three of the tests below exist to
say that this service does not.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx

from adrobot.application.errors import (
    UpstreamError,
    UpstreamNotFoundError,
    UpstreamProtocolError,
    UpstreamUnavailableError,
)
from adrobot.infrastructure.keitaro.transport import (
    BACKOFF_CAP_SECONDS,
    KeitaroTransport,
    _retry_delay,
    build_keitaro_client,
    keitaro_transport,
)
from tests.helpers import VALID_ENVIRONMENT, records_named

if TYPE_CHECKING:
    from io import StringIO

    from adrobot.settings import Settings

BASE = VALID_ENVIRONMENT["ADROBOT_KEITARO_BASE_URL"]
THE_KEY = VALID_ENVIRONMENT["ADROBOT_KEITARO_API_KEY"]

# A campaign object as the tracker really answers with one: the Click API token is inside
# it. Any test that lets this reach a log fails on the string, not on a judgement call.
A_TOKEN = "kt-click-api-token-93212"
A_CAMPAIGN = {"id": 93212, "name": "Demo", "token": A_TOKEN}


@pytest.fixture
def transport(settings: Settings) -> KeitaroTransport:
    """A transport over a real client, with the waiting taken out of the backoff."""
    return KeitaroTransport(build_keitaro_client(settings), backoff=0.0)


async def test_a_read_is_retried_until_the_tracker_answers() -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.get("/campaigns").mock(
            side_effect=[httpx.Response(500), httpx.Response(502), httpx.Response(200, json=[])]
        )
        client = httpx.AsyncClient(base_url=BASE)

        response = await KeitaroTransport(client, backoff=0.0).get("/campaigns")

    assert (response.status_code, route.call_count) == (200, 3)


async def test_a_read_gives_up_after_the_third_attempt() -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.get("/campaigns").mock(return_value=httpx.Response(503))

        with pytest.raises(UpstreamUnavailableError) as raised:
            await KeitaroTransport(httpx.AsyncClient(base_url=BASE), backoff=0.0).get("/campaigns")

    assert route.call_count == 3
    assert raised.value.status == 503


async def test_a_create_is_never_retried(transport: KeitaroTransport) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/campaigns").mock(return_value=httpx.Response(500))

        with pytest.raises(UpstreamUnavailableError):
            await transport.post("/campaigns", json={"name": "Demo"})

    assert route.call_count == 1, "a create that may have succeeded must not be sent twice"


async def test_a_create_that_timed_out_is_not_retried_either(transport: KeitaroTransport) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/campaigns").mock(side_effect=httpx.ReadTimeout("too slow"))

        with pytest.raises(UpstreamUnavailableError, match="ReadTimeout"):
            await transport.post("/campaigns", json={"name": "Demo"})

    assert route.call_count == 1, "the outcome is unknown, which is not the same as failed"


async def test_a_refused_connection_is_not_asked_again(transport: KeitaroTransport) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.get("/offers").mock(side_effect=httpx.ConnectError("refused"))

        with pytest.raises(UpstreamUnavailableError, match="ConnectError"):
            await transport.get("/offers")

    assert route.call_count == 1, (
        "a refused connection is a configuration fact, and three attempts would be three "
        "chances for the admin key to reach whatever is answering on that address"
    )


async def test_a_report_is_retried_although_it_is_a_post(transport: KeitaroTransport) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/report/build").mock(
            side_effect=[httpx.Response(503), httpx.Response(200, json={"rows": []})]
        )

        response = await transport.query("/report/build", json={"dimensions": []})

    assert (response.status_code, route.call_count) == (200, 2)


async def test_a_flow_is_written_again_after_a_timeout(transport: KeitaroTransport) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.put("/streams/564221").mock(
            side_effect=[httpx.ReadTimeout("too slow"), httpx.Response(200, json={})]
        )

        response = await transport.put("/streams/564221", json={"offers": []})

    # Safe by construction: the body is the whole intended state of the flow, so sending
    # it twice leaves the tracker where sending it once would have.
    assert (response.status_code, route.call_count) == (200, 2)


async def test_a_refusal_is_raised_at_once(transport: KeitaroTransport) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.get("/campaigns/93212").mock(return_value=httpx.Response(404))

        with pytest.raises(UpstreamNotFoundError):
            await transport.get("/campaigns/93212")

    assert route.call_count == 1, "a 404 is an answer, not a bad minute"


async def test_a_redirect_is_an_answer_and_not_somewhere_to_go(
    transport: KeitaroTransport, log_stream: StringIO
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.get("/campaigns").mock(
            return_value=httpx.Response(302, headers={"location": "https://elsewhere.invalid/"})
        )

        with pytest.raises(UpstreamProtocolError, match=r"elsewhere\.invalid"):
            await transport.get("/campaigns")

    assert route.call_count == 1, (
        "following this would carry the Api-Key header to elsewhere.invalid"
    )
    assert records_named(log_stream, "keitaro.request")[0]["location"] == (
        "https://elsewhere.invalid/"
    )


async def test_no_more_than_five_calls_are_in_flight_at_once(settings: Settings) -> None:
    in_flight = 0
    high_water = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal in_flight, high_water
        in_flight += 1
        high_water = max(high_water, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(handler))
    transport = KeitaroTransport(client, backoff=0.0)

    async with asyncio.TaskGroup() as group:
        for _ in range(20):
            group.create_task(transport.get("/offers"))

    assert high_water <= 5, "the tracker is somebody's production, and this is the polite half"


async def test_a_successful_body_never_reaches_the_log(
    transport: KeitaroTransport, log_stream: StringIO
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        mock.post("/campaigns").mock(return_value=httpx.Response(200, json=A_CAMPAIGN))

        await transport.post("/campaigns", json={"name": "Demo"})

    record = records_named(log_stream, "keitaro.request")[0]
    assert record["status"] == 200
    assert "body" not in record, "a 2xx body is where the Click API token is guaranteed to be"
    assert A_TOKEN not in log_stream.getvalue()


async def test_a_refused_body_is_logged_with_its_secrets_taken_out(
    transport: KeitaroTransport, log_stream: StringIO
) -> None:
    refusal: dict[str, Any] = {"error": "alias is already taken", "campaign": A_CAMPAIGN}
    async with respx.mock(base_url=BASE) as mock:
        mock.post("/campaigns").mock(return_value=httpx.Response(406, json=refusal))

        with pytest.raises(UpstreamError):
            await transport.post("/campaigns", json={"name": "Demo"})

    record = records_named(log_stream, "keitaro.request")[0]
    assert record["body"]["error"] == "alias is already taken", (
        "the reason is the point of the line"
    )
    assert record["body"]["campaign"]["token"] == "[redacted]"
    assert A_TOKEN not in log_stream.getvalue()


async def test_a_body_that_is_not_json_at_all_is_clipped_rather_than_dropped(
    transport: KeitaroTransport, log_stream: StringIO
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        mock.get("/campaigns").mock(return_value=httpx.Response(502, html="<h1>Bad Gateway</h1>"))

        with pytest.raises(UpstreamUnavailableError):
            await transport.get("/campaigns")

    assert "Bad Gateway" in records_named(log_stream, "keitaro.request")[0]["body"]


async def test_the_admin_key_reaches_the_tracker_and_nothing_else(
    transport: KeitaroTransport, log_stream: StringIO
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.get("/offers").mock(side_effect=[httpx.Response(500), httpx.Response(401)])

        with pytest.raises(UpstreamError):
            await transport.get("/offers")

    assert route.calls[0].request.headers["Api-Key"] == THE_KEY
    assert THE_KEY not in log_stream.getvalue(), (
        "httpx.Headers obfuscates authorization and nothing else, so a logged request "
        "object would print this in full"
    )
    assert len(records_named(log_stream, "keitaro.request")) == 2, (
        "one line per attempt, including the one that was retried"
    )


def test_the_client_is_built_the_way_the_tracker_is_talked_to(settings: Settings) -> None:
    client = build_keitaro_client(settings)

    assert client.follow_redirects is False
    assert client.headers["Api-Key"] == THE_KEY
    assert client.timeout.connect == 3.0
    assert str(client.base_url).startswith(BASE)


async def test_the_context_manager_closes_the_socket_it_opened(settings: Settings) -> None:
    async with keitaro_transport(settings) as transport:
        # Reaching into the transport on purpose: the claim is about this exact object.
        opened = transport._client
        assert not opened.is_closed

    assert opened.is_closed


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [("2", 2.0), ("0", 0.0), ("999", BACKOFF_CAP_SECONDS), ("-1", 0.0)],
)
def test_a_retry_after_the_tracker_sent_wins(retry_after: str, expected: float) -> None:
    assert _retry_delay(1, retry_after, 0.25) == expected


def test_a_retry_after_as_an_http_date_falls_back_to_the_backoff() -> None:
    assert 0 <= _retry_delay(1, "Wed, 21 Oct 2026 07:28:00 GMT", 0.25) <= 0.25


@pytest.mark.parametrize(
    ("attempt", "ceiling"), [(1, 0.25), (2, 0.5), (3, 1.0), (9, BACKOFF_CAP_SECONDS)]
)
def test_the_backoff_grows_and_is_jittered_across_the_whole_interval(
    attempt: int, ceiling: float
) -> None:
    delays = [_retry_delay(attempt, None, 0.25) for _ in range(50)]

    assert all(0 <= delay <= ceiling for delay in delays)
    assert min(delays) < ceiling / 2, "jitter across the interval, not around the ceiling"
