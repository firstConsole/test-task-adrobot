from __future__ import annotations

import json
import logging
import sys
from io import StringIO

import pytest
import structlog
from pydantic import SecretStr

from adrobot.logging import (
    configure_logging,
    correlation_id,
    correlation_id_scope,
    redact,
    sanitize_correlation_id,
)
from tests.helpers import log_records


def test_a_record_is_one_json_object_with_the_expected_envelope(log_stream: StringIO) -> None:
    structlog.stdlib.get_logger("adrobot.probe").info("kt.request", status=204)

    record = log_records(log_stream)[0]

    assert record["event"] == "kt.request"
    assert record["level"] == "info"
    assert record["logger"] == "adrobot.probe"
    assert record["status"] == 204
    assert record["timestamp"].endswith("Z")


def test_the_console_renderer_is_not_json() -> None:
    # The regression this catches is a chain that renders JSON in both modes, which is
    # invisible until someone runs the service locally and cannot read their own log.
    buffer = StringIO()
    configure_logging(level="INFO", renderer="console", stream=buffer)
    structlog.stdlib.get_logger("adrobot.probe").info("hello", answer=42)

    line = buffer.getvalue()

    assert "hello" in line
    assert "answer" in line
    with pytest.raises(json.JSONDecodeError):
        json.loads(line)


def test_a_stdlib_logger_comes_out_in_the_same_shape(log_stream: StringIO) -> None:
    # uvicorn, alembic and SQLAlchemy never touch structlog. Without the bridge a
    # container emits JSON on half its lines and plain text on the other half.
    logging.getLogger("uvicorn.error").info("Application startup complete.")

    record = log_records(log_stream)[0]

    assert record["event"] == "Application startup complete."
    assert record["logger"] == "uvicorn.error"
    assert record["level"] == "info"


def test_a_foreign_records_extra_survives_and_is_redacted(log_stream: StringIO) -> None:
    logging.getLogger("uvicorn.error").info(
        "started", extra={"revision": "0001_core", "api_key": "leaked"}
    )

    record = log_records(log_stream)[0]

    assert record["revision"] == "0001_core"
    assert record["api_key"] == "[redacted]"


def test_uvicorns_ansi_duplicate_of_its_own_message_is_dropped(log_stream: StringIO) -> None:
    # uvicorn attaches extra={"color_message": ...}: the same sentence again, with ANSI
    # escapes in it. In a JSON stream that is terminal control codes inside a field.
    logging.getLogger("uvicorn.error").info(
        "Started server process [%d]",
        4242,
        extra={"color_message": "Started server process [\x1b[36m%d\x1b[0m]"},
    )

    record = log_records(log_stream)[0]

    assert record["event"] == "Started server process [4242]"
    assert "color_message" not in record


def test_the_level_threshold_is_applied(log_stream: StringIO) -> None:
    logging.getLogger().setLevel(logging.WARNING)

    structlog.stdlib.get_logger("adrobot.probe").info("dropped")
    structlog.stdlib.get_logger("adrobot.probe").warning("kept")

    assert [record["event"] for record in log_records(log_stream)] == ["kept"]


def test_httpx_is_quiet_because_its_info_line_carries_the_whole_url(
    log_stream: StringIO,
) -> None:
    # httpx logs `HTTP Request: GET <full url>` at INFO, query values included. From 4.3
    # that logger is our Keitaro client, and §10 puts method/path/status on our own event.
    logging.getLogger("httpx").info("HTTP Request: GET https://t/x?token=leak")
    logging.getLogger("httpx").warning("kept")

    assert [record["event"] for record in log_records(log_stream)] == ["kept"]


def test_the_correlation_id_reaches_a_record_and_is_absent_otherwise(
    log_stream: StringIO,
) -> None:
    with correlation_id_scope("abcdef123456"):
        structlog.stdlib.get_logger("adrobot.probe").info("inside")
    structlog.stdlib.get_logger("adrobot.probe").info("outside")

    inside, outside = log_records(log_stream)

    assert inside["correlation_id"] == "abcdef123456"
    # Absent, not null: 6.7 renders an absent field rather than the string "None".
    assert "correlation_id" not in outside


def test_the_scope_restores_the_previous_id() -> None:
    assert correlation_id() is None
    with correlation_id_scope("outer-id-0001"), correlation_id_scope("inner-id-0002"):
        assert correlation_id() == "inner-id-0002"
    assert correlation_id() is None


@pytest.mark.parametrize(
    "hostile",
    [
        "",
        "short",
        "has spaces in it",
        'x" , "level": "info", "forged": "yes',
        "line\nbreak-injection",
        "x" * 200,
    ],
)
def test_an_unusable_inbound_id_is_replaced_whole(hostile: str) -> None:
    # The value is echoed into a header and into a newline-delimited JSON stream, so a
    # half-accepted id is worse than a generated one.
    assert sanitize_correlation_id(hostile) != hostile


def test_a_usable_inbound_id_is_kept() -> None:
    # nginx's $request_id is 32 hex characters; that is the value this has to preserve,
    # because it is what joins the proxy's access log to ours (9.8).
    assert sanitize_correlation_id("a" * 32) == "a" * 32


def test_redact_hides_a_nested_keitaro_campaign_token() -> None:
    # PLAN-BACKEND §10: Keitaro returns a campaign's Click API token inside the campaign
    # object, and a non-2xx body is logged.
    body = {"campaign": {"id": 93212, "token": "kt-click-token", "streams": [{"token": "b"}]}}

    assert redact(body) == {
        "campaign": {"id": 93212, "token": "[redacted]", "streams": [{"token": "[redacted]"}]}
    }


def test_redact_leaves_the_argument_alone() -> None:
    # It is called on a parsed response body that the caller goes on to use.
    body = {"api_key": "secret"}

    redact(body)

    assert body == {"api_key": "secret"}


def test_redact_keeps_what_is_not_secret() -> None:
    # A redactor that eats the whole record is indistinguishable from no logging at all.
    assert redact({"offer_id": 11234, "share": 34}) == {"offer_id": 11234, "share": 34}


def test_redact_leaves_a_non_string_key_alone() -> None:
    # A logger must never be the thing that takes a request down. JSON keys are always
    # strings, so this cannot arrive from a Keitaro body — but `redact` is public.
    assert redact({1: "not a secret marker"}) == {1: "not a secret marker"}


def test_a_traceback_survives_redaction(log_stream: StringIO) -> None:
    # `exc_info` reaches the processor chain as a three-tuple, and `format_exc_info`
    # dispatches on its type: a redactor that rebuilt it as a list would silently swallow
    # every traceback in the process.
    try:
        message = "deliberate"
        raise RuntimeError(message)  # noqa: TRY301 — the raise is what is under test
    except RuntimeError:
        structlog.stdlib.get_logger("adrobot.probe").error("boom", exc_info=sys.exc_info())

    assert "RuntimeError: deliberate" in log_records(log_stream)[0]["exception"]


def test_redact_stops_before_a_cycle_takes_the_process_down() -> None:
    cycle: dict[str, object] = {}
    cycle["self"] = cycle

    assert "[too deep]" in json.dumps(redact(cycle))


def test_a_secretstr_in_a_record_renders_masked(log_stream: StringIO) -> None:
    structlog.stdlib.get_logger("adrobot.probe").info("probe", value=SecretStr("hunter2"))

    assert "hunter2" not in log_stream.getvalue()
