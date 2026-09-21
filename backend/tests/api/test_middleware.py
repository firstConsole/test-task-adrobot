from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import anyio
import httpx
import pytest
from starlette.responses import Response

from adrobot.api.app import MAX_REQUEST_BODY_BYTES, create_app
from adrobot.api.middleware import CORRELATION_ID_HEADER
from adrobot.logging import correlation_id
from tests.helpers import records_named
from tests.wiring import fake_ports_factory

if TYPE_CHECKING:
    from collections.abc import MutableMapping
    from io import StringIO

    from fastapi import FastAPI

    from adrobot.settings import Settings

ACCESS_EVENT = "http.request"


async def test_an_inbound_id_is_reused_and_echoed(client: httpx.AsyncClient) -> None:
    # nginx generates $request_id and passes it on (9.8); reusing it is what joins the
    # proxy's access log to ours.
    response = await client.get("/healthz", headers={CORRELATION_ID_HEADER: "a" * 32})

    assert response.headers[CORRELATION_ID_HEADER] == "a" * 32


async def test_an_id_is_generated_when_the_caller_sends_none(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers[CORRELATION_ID_HEADER])


@pytest.mark.parametrize("hostile", ["", "short", 'x", "forged": "yes', "x" * 200])
async def test_an_unusable_inbound_id_is_not_echoed_back(
    client: httpx.AsyncClient, hostile: str
) -> None:
    # The value goes into a newline-delimited JSON stream and back out in a header. httpx
    # sends all of these happily, so this is a real hole rather than a hypothetical one.
    response = await client.get("/healthz", headers={CORRELATION_ID_HEADER: hostile})

    assert response.headers[CORRELATION_ID_HEADER] != hostile


async def test_the_id_does_not_leak_between_concurrent_requests(settings: Settings) -> None:
    # The test that a module-level global would pass every other way: eight requests are
    # held inside the handler at once, so the last writer's id would be handed to all of
    # them. 6.7 reads exactly this value when it renders problem+json.
    arrived = anyio.Semaphore(0)
    release = anyio.Event()
    app = create_app(settings=settings, ports_factory=fake_ports_factory())
    seen: dict[str, str | None] = {}

    @app.get("/_probe/{name}")
    async def probe(name: str) -> dict[str, str | None]:
        arrived.release()
        await release.wait()
        return {"correlation_id": correlation_id()}

    async def call(http: httpx.AsyncClient, name: str) -> None:
        response = await http.get(f"/_probe/{name}", headers={CORRELATION_ID_HEADER: name * 8})
        seen[name] = response.json()["correlation_id"]

    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://adrobot.test") as http,
        anyio.create_task_group() as requests,
    ):
        for name in "abcdefgh":
            requests.start_soon(call, http, name)
        for _ in "abcdefgh":
            await arrived.acquire()
        release.set()

    assert seen == {name: name * 8 for name in "abcdefgh"}


async def test_one_access_record_per_request_with_the_fields_that_matter(
    client: httpx.AsyncClient, log_stream: StringIO
) -> None:
    await client.get("/does-not-exist", headers={CORRELATION_ID_HEADER: "b" * 32})

    record = records_named(log_stream, ACCESS_EVENT)[0]

    assert record["method"] == "GET"
    assert record["path"] == "/does-not-exist"
    # A 404 is logged too, and above INFO: a middleware that only logs the success path is
    # the one that hides the outage.
    assert record["status"] == 404
    assert record["level"] == "warning"
    assert record["duration_ms"] >= 0
    assert record["correlation_id"] == "b" * 32


async def test_no_request_header_reaches_the_access_record(
    client: httpx.AsyncClient, log_stream: StringIO
) -> None:
    # `Authorization` and `Cookie` both carry §9's shared token, and the regression this
    # catches is the one-liner `headers=dict(request.headers)`.
    await client.get(
        "/healthz",
        headers={"Authorization": "Bearer the-shared-token", "Cookie": "session=the-shared-token"},
    )

    assert "the-shared-token" not in log_stream.getvalue()


async def test_no_query_value_reaches_the_access_record(
    client: httpx.AsyncClient, log_stream: StringIO
) -> None:
    await client.get("/healthz", params={"q": "secret-search-term"})

    assert "secret-search-term" not in log_stream.getvalue()


async def test_a_health_probe_is_logged_at_debug(
    client: httpx.AsyncClient, log_stream: StringIO
) -> None:
    # A compose healthcheck hits this every few seconds; at INFO it would be the only
    # thing in the log. A probe that stops answering 2xx leaves the quiet branch at once.
    await client.get("/healthz")

    assert records_named(log_stream, ACCESS_EVENT)[0]["level"] == "debug"


async def test_an_unhandled_exception_is_logged_with_its_traceback_and_re_raised(
    settings: Settings, log_stream: StringIO
) -> None:
    app = create_app(settings=settings, ports_factory=fake_ports_factory())

    @app.get("/_boom")
    async def boom() -> None:
        message = "deliberate"
        raise RuntimeError(message)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://adrobot.test") as http:
        await http.get("/_boom")

    record = records_named(log_stream, ACCESS_EVENT)[0]

    assert record["status"] == 500
    assert record["level"] == "error"
    assert "RuntimeError: deliberate" in record["exception"]


async def test_a_returned_server_error_is_logged_at_error_too(
    settings: Settings, log_stream: StringIO
) -> None:
    # Not every 500 arrives as an exception. From 6.7 onward the RFC 9457 handlers turn a
    # failure into a *returned* response, and that is the shape this branch serves.
    app = create_app(settings=settings, ports_factory=fake_ports_factory())

    @app.get("/_unavailable")
    async def unavailable() -> Response:
        return Response(status_code=503)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://adrobot.test") as http:
        await http.get("/_unavailable")

    assert records_named(log_stream, ACCESS_EVENT)[0]["level"] == "error"


async def test_a_body_over_the_limit_is_refused_before_it_is_read(
    client: httpx.AsyncClient,
) -> None:
    # POSTed at a GET-only route on purpose: the limit answers 413 rather than the 405
    # routing would give, which is what proves it runs before the application does. There
    # is no write endpoint until 6.9, and this is the cheapest way to keep the guarantee
    # honest until there is.
    response = await client.post("/healthz", content=b"x" * (MAX_REQUEST_BODY_BYTES + 1))

    assert response.status_code == 413


async def test_a_body_under_the_limit_reaches_the_router(client: httpx.AsyncClient) -> None:
    assert (await client.post("/healthz", content=b"x" * 16)).status_code == 405


async def test_the_lifespan_protocol_survives_the_middleware_stack(app: FastAPI) -> None:
    # uvicorn sends `{"type": "lifespan"}` through the whole middleware stack, so the
    # non-HTTP branch of both classes is on the start-up path of every real run — and it
    # is the one branch `httpx.ASGITransport` never exercises. A middleware that assumed
    # `scope["method"]` would take the process down before it served anything.
    incoming = ["lifespan.startup", "lifespan.shutdown"]
    sent: list[str] = []

    async def receive() -> MutableMapping[str, Any]:
        return {"type": incoming.pop(0)}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(str(message["type"]))

    await app({"type": "lifespan"}, receive, send)

    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
