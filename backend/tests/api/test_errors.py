"""That every failure leaves this API in one shape, and that the shape gives nothing away.

Two of these are worth more than the rest. The walk over both exception hierarchies is what
makes the table in `api/errors.py` complete on the day somebody adds an error rather than on
the day somebody hits one. And the validation test asserts an *absence*: FastAPI's own
handler puts what you sent into `input`, so the endpoint that refuses a token is the
endpoint that hands it back.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from adrobot.api.app import create_app
from adrobot.api.errors import PROBLEMS, WITHHELD
from adrobot.api.middleware import CORRELATION_ID_HEADER
from adrobot.api.schemas.problem import PROBLEM_MEDIA_TYPE, PROBLEM_TYPE_PREFIX
from adrobot.application.errors import (
    ApplicationError,
    CampaignAlreadyImportedError,
    CampaignNotFoundError,
    UpstreamDeniedError,
    UpstreamRejectedError,
)
from adrobot.domain.errors import DomainError, InvalidCountryCodeError
from adrobot.domain.ids import CampaignId, KeitaroCampaignId
from adrobot.settings import Settings
from tests.helpers import records_named
from tests.wiring import fake_ports_factory

if TYPE_CHECKING:
    from io import StringIO

    import pytest

SLUG = re.compile(r"\A[a-z][a-z0-9-]*\Z")
RAISE = "/_raise"
SUBMIT = "/_submit"


class Submission(BaseModel):
    """A body with one field, so that a refusal has somewhere to point."""

    country: str


def probes(raised: Exception) -> APIRouter:
    router = APIRouter()

    @router.get(RAISE)
    async def failing() -> None:
        raise raised

    @router.post(SUBMIT)
    async def submitting(body: Submission) -> dict[str, str]:
        return {"country": body.country}

    return router


def application(raised: Exception, *, settings: Settings) -> FastAPI:
    app = create_app(settings=settings, ports_factory=fake_ports_factory())
    app.include_router(probes(raised))
    return app


def calling(app: FastAPI, *, raising: bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=raising),
        base_url="http://adrobot.test",
    )


async def failure(raised: Exception, *, settings: Settings) -> httpx.Response:
    async with calling(application(raised, settings=settings)) as http:
        return await http.get(RAISE)


def subclasses(root: type[Exception]) -> set[type[Exception]]:
    found = set(root.__subclasses__())
    return found | {deeper for child in found for deeper in subclasses(child)}


def test_every_error_either_ring_raises_has_a_rendering() -> None:
    # The table is complete by construction rather than by inspection: an error added at
    # stage 7 and left out of it fails here, not in whichever screen first raises it.
    unmapped = (subclasses(DomainError) | subclasses(ApplicationError)) - set(PROBLEMS)

    assert not unmapped, f"{sorted(error.__name__ for error in unmapped)} render as a bare 500"


def test_the_codes_are_slugs_and_no_two_errors_share_one() -> None:
    codes = [problem.code for problem in PROBLEMS.values()]

    # A client switches on these, so a duplicate is two failures it cannot tell apart and a
    # capital letter is a string somebody will get wrong once.
    assert all(SLUG.fullmatch(code) for code in codes)
    assert len(codes) == len(set(codes))


async def test_a_missing_campaign_answers_404_as_problem_json(settings: Settings) -> None:
    answer = await failure(CampaignNotFoundError(uuid4()), settings=settings)

    assert answer.status_code == 404
    assert answer.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    body = answer.json()
    assert body["code"] == "campaign-not-found"
    assert body["type"] == f"{PROBLEM_TYPE_PREFIX}campaign-not-found"
    assert body["status"] == 404
    assert body["title"] == "No such campaign"
    assert "no campaign" in body["detail"]


async def test_the_body_carries_the_correlation_id_from_the_response_header(
    settings: Settings,
) -> None:
    async with calling(application(CampaignNotFoundError(uuid4()), settings=settings)) as http:
        answer = await http.get(RAISE, headers={CORRELATION_ID_HEADER: "b" * 32})

    # The one value worth quoting in a bug report, and the same one in three places: the
    # header, every log line of this request, and here.
    assert answer.json()["correlation_id"] == "b" * 32
    assert answer.headers[CORRELATION_ID_HEADER] == "b" * 32


async def test_an_already_imported_campaign_names_the_one_that_exists(
    settings: Settings,
) -> None:
    existing = CampaignId(uuid4())

    answer = await failure(
        CampaignAlreadyImportedError(KeitaroCampaignId(93212), campaign_id=existing),
        settings=settings,
    )

    assert answer.status_code == 409
    # What lets the screen offer to open the campaign instead of only refusing the request.
    assert answer.json()["campaign_id"] == str(existing)


async def test_the_trackers_own_field_complaints_arrive_in_the_one_list(
    settings: Settings,
) -> None:
    answer = await failure(
        UpstreamRejectedError(
            "offer is not available", status=406, fields={"offer_id": ("does not exist",)}
        ),
        settings=settings,
    )

    # 422 and not 502: the tracker refuses a payload over a value the caller chose, and a
    # 502 would send somebody to check a service that is working.
    assert answer.status_code == 422
    assert answer.json()["errors"] == [
        {"location": "tracker.offer_id", "message": "does not exist"}
    ]


async def test_a_domain_refusal_answers_422_with_its_own_code(settings: Settings) -> None:
    answer = await failure(InvalidCountryCodeError("XX"), settings=settings)

    assert answer.status_code == 422
    assert answer.json()["code"] == "invalid-country"


async def test_a_5xx_says_what_happened_in_development(settings: Settings) -> None:
    answer = await failure(UpstreamDeniedError("401 Unauthorized", status=401), settings=settings)

    assert answer.status_code == 502
    assert "ADROBOT_KEITARO_API_KEY" in answer.json()["detail"]


async def test_a_5xx_withholds_what_happened_in_production(
    valid_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADROBOT_ENV", "prod")

    answer = await failure(UpstreamDeniedError("401 Unauthorized", status=401), settings=Settings())

    # The variable holding the tracker key is not a thing to print at somebody who asked to
    # create a campaign, and the correlation id is what they can do something with.
    assert answer.json()["detail"] == WITHHELD
    assert "ADROBOT_KEITARO_API_KEY" not in answer.text
    assert answer.json()["correlation_id"]


async def test_a_refused_body_names_the_field_and_never_repeats_what_was_sent(
    settings: Settings,
) -> None:
    async with calling(application(CampaignNotFoundError(uuid4()), settings=settings)) as http:
        answer = await http.post(SUBMIT, json={"country": 12345})

    body = answer.json()
    assert answer.status_code == 422
    assert answer.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert body["code"] == "invalid-request"
    assert [field["location"] for field in body["errors"]] == ["body.country"]
    # The assertion this test exists for. FastAPI's own handler renders `input` — which on
    # the endpoint that refuses a credential is the credential, in the response body.
    assert "12345" not in answer.text


async def test_an_unknown_path_and_a_wrong_method_answer_in_the_same_shape(
    settings: Settings,
) -> None:
    async with calling(application(CampaignNotFoundError(uuid4()), settings=settings)) as http:
        missing = await http.get("/_nowhere")
        wrong = await http.post(RAISE)

    assert missing.status_code == 404
    assert missing.json()["code"] == "not-found"
    assert missing.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert wrong.status_code == 405
    assert wrong.json()["code"] == "method-not-allowed"
    # Starlette puts the allowed methods on its own 405, and a renderer that dropped the
    # headers would leave a client guessing.
    assert "Allow" in wrong.headers


async def test_an_unhandled_bug_is_rendered_as_a_500_and_logged_with_its_traceback(
    settings: Settings, log_stream: StringIO
) -> None:
    app = application(RuntimeError("deliberate"), settings=settings)

    async with calling(app, raising=False) as http:
        answer = await http.get(RAISE)

    assert answer.status_code == 500
    assert answer.json()["code"] == "internal-error"
    problems: list[dict[str, Any]] = records_named(log_stream, "http.problem")
    assert problems[0]["status"] == 500
    assert "RuntimeError: deliberate" in problems[0]["exception"]
