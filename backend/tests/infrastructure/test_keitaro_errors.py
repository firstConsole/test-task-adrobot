"""What each answer from the tracker is turned into, and what survives the translation.

The distinctions here are the ones a person acts on. "The tracker refused our key" is a
variable to change; "the tracker rejected this alias" is a form field to correct; "the
tracker is not answering" is a reason to wait. Collapsing the three into one 502 is the
easy mistake, and it is the one that makes an operator read logs to learn what a screen
could have told them.
"""

from __future__ import annotations

import httpx
import pytest

from adrobot.application.errors import (
    UpstreamDeniedError,
    UpstreamError,
    UpstreamNotFoundError,
    UpstreamProtocolError,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from adrobot.infrastructure.keitaro.errors import raise_for_keitaro, unreachable_tracker

# The undocumented shape of a Keitaro 406: field names, each with its own complaints. No
# schema in the published document describes it, which is why errors.py reads it by hand.
A_VALIDATION_FAILURE = {"alias": ["has already been taken"], "name": ["is too long"]}


@pytest.mark.parametrize("status", [200, 201, 204])
def test_a_success_raises_nothing(status: int) -> None:
    # 201 among them because `DELETE /campaigns` — an archive, in this API — answers with
    # one, alone in the whole document.
    raise_for_keitaro(httpx.Response(status))


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, UpstreamRejectedError),
        (401, UpstreamDeniedError),
        # "Admin API is not available in that edition": a licence, not a bad minute.
        (402, UpstreamDeniedError),
        (403, UpstreamDeniedError),
        (404, UpstreamNotFoundError),
        (406, UpstreamRejectedError),
        (422, UpstreamRejectedError),
        (500, UpstreamUnavailableError),
        (503, UpstreamUnavailableError),
        # A status nothing here has thought about, which is itself worth saying out loud.
        (418, UpstreamProtocolError),
    ],
)
def test_each_status_is_named_for_what_a_person_would_do_about_it(
    status: int, expected: type[UpstreamError]
) -> None:
    with pytest.raises(expected) as raised:
        raise_for_keitaro(httpx.Response(status, json={"error": "no"}))

    assert raised.value.status == status


def test_a_refused_key_says_which_variable_holds_it() -> None:
    with pytest.raises(UpstreamDeniedError, match="ADROBOT_KEITARO_API_KEY"):
        raise_for_keitaro(httpx.Response(401, json={"error": "Access denied"}))


def test_a_validation_failure_keeps_the_tracker_s_complaint_per_field() -> None:
    with pytest.raises(UpstreamRejectedError) as raised:
        raise_for_keitaro(httpx.Response(406, json=A_VALIDATION_FAILURE))

    assert raised.value.fields == {
        "alias": ("has already been taken",),
        "name": ("is too long",),
    }
    assert "has already been taken" in str(raised.value), (
        "the summary carries it too, so a log line alone answers what went wrong"
    )


def test_a_documented_error_body_is_quoted_as_it_stands() -> None:
    with pytest.raises(UpstreamNotFoundError, match="Campaign not found"):
        raise_for_keitaro(httpx.Response(404, json={"error": "Campaign not found"}))


def test_every_other_failure_carries_no_fields_at_all() -> None:
    with pytest.raises(UpstreamUnavailableError) as raised:
        raise_for_keitaro(httpx.Response(500, json={"error": "oops"}))

    # So that a renderer can read `.fields` off any upstream error without a type check.
    assert raised.value.fields == {}


def test_a_body_that_is_not_json_is_still_reported() -> None:
    with pytest.raises(UpstreamUnavailableError, match="Bad Gateway"):
        raise_for_keitaro(httpx.Response(502, html="<h1>Bad Gateway</h1>"))


def test_an_empty_body_says_that_it_was_empty() -> None:
    with pytest.raises(UpstreamUnavailableError, match="no body"):
        raise_for_keitaro(httpx.Response(503))


def test_a_shape_nobody_planned_for_is_reported_rather_than_swallowed() -> None:
    with pytest.raises(UpstreamRejectedError, match="surprise"):
        raise_for_keitaro(httpx.Response(400, json=["surprise"]))


def test_an_object_that_is_neither_shape_is_quoted_whole() -> None:
    # Neither `{"error": ...}` nor field-to-complaints. Reporting the object as it stands
    # beats reporting nothing, which is what a stricter reader would have to do.
    with pytest.raises(UpstreamRejectedError, match="reason_code"):
        raise_for_keitaro(httpx.Response(400, json={"reason_code": 7, "ok": False}))


def test_what_the_tracker_said_is_clipped_before_it_reaches_a_log_line() -> None:
    with pytest.raises(UpstreamUnavailableError) as raised:
        raise_for_keitaro(httpx.Response(500, json={"error": "x" * 5000}))

    assert len(str(raised.value)) < 500


def test_a_redirect_is_reported_with_where_it_wanted_to_go() -> None:
    answer = httpx.Response(302, headers={"location": "https://elsewhere.invalid/"})

    with pytest.raises(UpstreamProtocolError, match=r"elsewhere\.invalid"):
        raise_for_keitaro(answer)


def test_a_redirect_without_a_location_is_still_a_redirect() -> None:
    with pytest.raises(UpstreamProtocolError):
        raise_for_keitaro(httpx.Response(302))


def test_a_failure_that_never_became_an_answer_names_the_call_and_not_the_url() -> None:
    # str(exc) on an httpx error carries the full request URL. The class name is enough to
    # tell a timeout from a refused connection, and the base URL stays out of the message.
    failure = unreachable_tracker("GET", "/offers", httpx.ConnectError("[Errno 111] to 10.0.0.1"))

    assert isinstance(failure, UpstreamUnavailableError)
    assert "ConnectError" in str(failure)
    assert "10.0.0.1" not in str(failure)
    assert failure.status is None, "there was no status: the tracker never answered"
