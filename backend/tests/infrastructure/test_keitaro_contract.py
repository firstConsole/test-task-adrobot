"""Two claims that hold the adapter to something outside itself.

The first is that every path it calls is a path the tracker publishes. A wrapper's most
embarrassing failure is an endpoint that does not exist — the schema spells the action
catalogue `/streams_actions` while two shipping clients call it `/stream_actions`, and only
one of the two spellings answers. That class of mistake is invisible against a mock, which
will answer anything, so this drives every method of both ports through a catch-all and
holds the paths it collects against `docs/keitaro-openapi.json`.

The second is that the two implementations of `KeitaroAdminPort` agree. `tests/fakes.py` is
what every scenario of stages 6 and 7 is written against, so anywhere it and the HTTP
adapter differ, a suite of green scenario tests means nothing. What is compared is the
push — the only method with a decision in it — under both of the semantics `PUT
/streams/{id}` might have, because the push is meant to be right in either world.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from zoneinfo import ZoneInfo

import httpx
import pytest

from adrobot.domain.campaign import CampaignBlueprint
from adrobot.domain.diff import DesiredOffer
from adrobot.domain.ids import KeitaroCampaignId, KeitaroStreamId, OfferId
from adrobot.domain.stream import StreamSchema, StreamSpec, StreamType
from adrobot.domain.values import CampaignAlias, CampaignName, OfferState
from adrobot.infrastructure.keitaro.admin import HttpKeitaroAdmin
from adrobot.infrastructure.keitaro.reports import HttpKeitaroReports
from adrobot.infrastructure.keitaro.transport import KeitaroTransport
from tests.fakes import FakeKeitaroAdmin
from tests.helpers import VALID_ENVIRONMENT

if TYPE_CHECKING:
    from adrobot.application.ports.keitaro import KeitaroAdminPort

BASE = VALID_ENVIRONMENT["ADROBOT_KEITARO_BASE_URL"]
MADRID = ZoneInfo("Europe/Madrid")
SPEC_PATH: Final = Path(__file__).resolve().parents[3] / "docs" / "keitaro-openapi.json"

CAMPAIGN_ID = KeitaroCampaignId(93212)
STREAM_ID = KeitaroStreamId(564221)
A_DAY = date(2026, 9, 20)

# The state the push tests start from: the two offers of the video's clean flow, whose
# shares add up to 50 and are left exactly as the tracker holds them.
STARTING_STATE: Final = ((3749, 50), (11112, 50))


@lru_cache(maxsize=1)
def _spec() -> dict[str, Any]:
    loaded = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _desired(offer_id: int, share: int, state: OfferState = OfferState.ACTIVE) -> DesiredOffer:
    return DesiredOffer(offer_id=OfferId(offer_id), share=share, state=state)


def _stream_spec(*offers: DesiredOffer) -> StreamSpec:
    return StreamSpec(
        campaign_id=CAMPAIGN_ID,
        name="Flow 2",
        type=StreamType.REGULAR,
        schema=StreamSchema.LANDINGS,
        action_type="http",
        position=2,
        offers=offers,
    )


def _templated(path: str) -> str:
    """Put a request's path back into the shape the schema declares it in.

    `/admin_api/v1/campaigns/93212/streams` is `/campaigns/{id}/streams` in the document,
    and every identifier in this API is an integer — so a numeric segment is an `{id}`.
    """
    inside = path.removeprefix(str(_spec()["servers"][0]["url"]))
    return "/".join("{id}" if part.isdigit() else part for part in inside.split("/"))


def _transport(handler: Any) -> KeitaroTransport:
    client = httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(handler))
    return KeitaroTransport(client, backoff=0.0)


class _AnythingTracker:
    """A tracker that answers every request, plausibly enough for the mappers to read it."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = _templated(request.url.path)
        self.asked.append((request.method, path))
        return httpx.Response(200, json=self._body(request.method, path))

    def _body(self, method: str, path: str) -> object:
        if path == "/report/build":
            return {"rows": []}
        if path == "/campaigns/{id}/streams":
            return []
        if method == "GET" and path in {"/offers", "/groups", "/traffic_sources", "/domains"}:
            return []
        if path in {"/streams", "/streams/{id}"}:
            return _wire_flow()
        return {"id": CAMPAIGN_ID, "name": "Anything"}


UNPUBLISHED = {"GET /settings"}
"""The one path this adapter calls that the published schema has never declared.

Deliberate, and narrow: the tracker's own time zone is not in the document and not derivable
from anything that is, and a build without the path answers 404 — which
`application/time_zone.py` reads as "the configured zone stands" rather than as a failure.
"""


async def test_every_path_the_adapter_calls_is_one_the_tracker_publishes() -> None:
    answers = _AnythingTracker()
    transport = _transport(answers)
    admin = HttpKeitaroAdmin(transport, zone=MADRID)
    reports = HttpKeitaroReports(transport)

    await admin.list_reference_data()
    await admin.create_campaign_group("AD Robot")
    await admin.create_campaign(
        CampaignBlueprint(
            name=CampaignName("Summer MX"), alias=CampaignAlias("summer-mx"), group_id=7
        )
    )
    await admin.get_campaign(CAMPAIGN_ID)
    await admin.create_stream(_stream_spec())
    await admin.update_stream(STREAM_ID, _stream_spec())
    await admin.list_campaign_streams(CAMPAIGN_ID)
    await admin.replace_stream_offers(STREAM_ID, ())
    await admin.list_offers()
    await admin.get_time_zone()
    await reports.clicks_by_stream(CAMPAIGN_ID, A_DAY, timezone="Europe/Madrid")
    await reports.clicks_by_offer(CAMPAIGN_ID, A_DAY, timezone="Europe/Madrid")

    published = _spec()["paths"]
    invented = sorted(
        f"{method} {path}"
        for method, path in set(answers.asked)
        if method.lower() not in published.get(path, {})
    )

    # Exactly one, and it is the one this service knows it is guessing about. An equality
    # and not `not invented`: the schema is the contract everything else is written
    # against, and a second undocumented path added later should have to be argued for
    # here rather than inherited from an assertion that had already been relaxed.
    assert invented == sorted(UNPUBLISHED), f"the schema publishes no such endpoint: {invented}"
    assert len(set(answers.asked)) >= 11, "and every method of both ports was exercised"


def _wire_flow(*offers: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": STREAM_ID,
        "campaign_id": CAMPAIGN_ID,
        "name": "Flow 2",
        "type": "regular",
        "schema": "landings",
        "action_type": "http",
        "collect_clicks": True,
        "filters": [{"id": 3, "name": "country", "mode": "accept", "payload": ["AU"]}],
        "offers": list(offers),
    }


class _WireTracker:
    """One flow, kept in Keitaro's own shapes, with one of the two update semantics.

    Written at the wire level rather than delegating to `FakeKeitaroAdmin`, so that the two
    implementations under comparison really are independent: everything the HTTP adapter
    does — the body it builds, the answer it reads, the verification it runs — has to agree
    with a tracker that knows nothing about it.
    """

    def __init__(self, *, merges: bool) -> None:
        self.merges = merges
        self.flow = _wire_flow(
            *(
                {
                    "id": offer_id * 10,
                    "offer_id": offer_id,
                    "share": share,
                    "state": "active",
                    "created_at": f"2026-09-20 12:0{index}:00",
                }
                for index, (offer_id, share) in enumerate(STARTING_STATE)
            )
        )
        self._row_ids = 900

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            self.flow = self._written(json.loads(request.content))
        return httpx.Response(200, json=self.flow)

    def _written(self, body: dict[str, Any]) -> dict[str, Any]:
        held = {row["offer_id"]: row for row in self.flow["offers"]}
        written: list[dict[str, Any]] = []
        for sent in body.get("offers", []):
            gone = sent["share"] == 0 and sent.get("state") == "disabled"
            if gone and not self.merges:
                continue
            existing = held.get(sent["offer_id"])
            if existing is None:
                self._row_ids += 1
                written.append({"id": self._row_ids, "created_at": "2026-09-20 13:00:00", **sent})
            else:
                written.append({**existing, **sent})
        return {**self.flow, **body, "offers": written}


@pytest.fixture(params=[False, True], ids=["a tracker that replaces", "a tracker that merges"])
def merges(request: pytest.FixtureRequest) -> bool:
    """Both answers to the one question `docs/keitaro-api-notes.md` §4 leaves open."""
    assert isinstance(request.param, bool)
    return request.param


@pytest.fixture(params=["in memory", "over http"])
async def admin(
    request: pytest.FixtureRequest,
    merges: bool,  # noqa: FBT001 — a fixture pytest fills in, not a flag anyone passes
) -> KeitaroAdminPort:
    """The port, implemented both ways, holding the same flow in the same starting state."""
    if request.param == "in memory":
        fake = FakeKeitaroAdmin(merges=merges)
        await fake.create_stream(_stream_spec(*(_desired(*row) for row in STARTING_STATE)))
        return fake
    return HttpKeitaroAdmin(_transport(_WireTracker(merges=merges)), zone=MADRID)


async def test_a_push_leaves_the_flow_holding_exactly_what_was_asked_for(
    admin: KeitaroAdminPort,
) -> None:
    written = await admin.replace_stream_offers(
        STREAM_ID, (_desired(3749, 34), _desired(11112, 33), _desired(22222, 33))
    )

    assert {row.offer_id: row.share for row in written.offers} == {3749: 34, 11112: 33, 22222: 33}


async def test_a_removed_offer_takes_no_traffic_whichever_way_the_tracker_works(
    admin: KeitaroAdminPort,
) -> None:
    written = await admin.replace_stream_offers(
        STREAM_ID, (_desired(3749, 100), _desired(11112, 0, OfferState.DISABLED))
    )

    # Two right answers, and the caller does not have to know which it got: a tracker that
    # replaces the array keeps no row, one that merges keeps it switched off.
    removed = [row for row in written.offers if row.offer_id == 11112]
    assert not removed or (removed[0].share, removed[0].state) == (0, "disabled")
    assert {row.offer_id: row.share for row in written.offers if row.share} == {3749: 100}


async def test_a_row_that_was_already_there_keeps_the_timestamp_the_tie_break_reads(
    admin: KeitaroAdminPort,
) -> None:
    before = await admin.replace_stream_offers(STREAM_ID, (_desired(3749, 100),))
    kept = next(row for row in before.offers if row.offer_id == 3749)

    after = await admin.replace_stream_offers(STREAM_ID, (_desired(3749, 50), _desired(22222, 50)))

    still = next(row for row in after.offers if row.offer_id == 3749)
    added = next(row for row in after.offers if row.offer_id == 22222)
    assert still.created_at == kept.created_at, "a push is not what makes a row new"
    assert still.created_at is not None
    assert added.created_at is not None
    assert added.created_at > still.created_at
