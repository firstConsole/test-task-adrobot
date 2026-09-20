"""The admin adapter against a mocked tracker: which calls it makes, and what it sends.

Most methods here are one path and one body. The ones worth reading are the five about
`replace_stream_offers`, which is the only method with a decision in it — and that decision
is what the whole project is judged on: after a push, does the tracker hold the numbers the
screen showed.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from adrobot.application.errors import UpstreamDeniedError, UpstreamProtocolError
from adrobot.domain.campaign import CampaignBlueprint
from adrobot.domain.diff import DesiredOffer
from adrobot.domain.ids import KeitaroCampaignId, KeitaroStreamId, OfferId
from adrobot.domain.stream import StreamSchema, StreamSpec, StreamType
from adrobot.domain.values import CampaignAlias, CampaignName, OfferState
from adrobot.infrastructure.keitaro.admin import HttpKeitaroAdmin
from adrobot.infrastructure.keitaro.transport import KeitaroTransport, build_keitaro_client
from tests.helpers import VALID_ENVIRONMENT

if TYPE_CHECKING:
    from respx.models import Route

    from adrobot.settings import Settings

BASE = VALID_ENVIRONMENT["ADROBOT_KEITARO_BASE_URL"]
MADRID = ZoneInfo("Europe/Madrid")

CAMPAIGN_ID = KeitaroCampaignId(93212)
STREAM_ID = KeitaroStreamId(564221)


def _flow(*offers: dict[str, Any]) -> dict[str, Any]:
    """The reference campaign's Flow 2: a geo filter, and whichever offers a test needs."""
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


def _row(offer_id: int, share: int, state: str = "active") -> dict[str, Any]:
    return {"id": offer_id * 10, "offer_id": offer_id, "share": share, "state": state}


def _desired(offer_id: int, share: int, state: OfferState = OfferState.ACTIVE) -> DesiredOffer:
    return DesiredOffer(offer_id=OfferId(offer_id), share=share, state=state)


def _sent(route: Route, index: int = 0) -> dict[str, Any]:
    """The body of one of a route's calls, as the tracker received it."""
    body = json.loads(route.calls[index].request.content)
    assert isinstance(body, dict)
    return body


@pytest.fixture
def admin(settings: Settings) -> HttpKeitaroAdmin:
    return HttpKeitaroAdmin(
        KeitaroTransport(build_keitaro_client(settings), backoff=0.0), zone=MADRID
    )


async def test_the_three_catalogues_are_read_from_their_own_endpoints(
    admin: HttpKeitaroAdmin,
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        groups = mock.get("/groups").mock(
            return_value=httpx.Response(200, json=[{"id": 7, "name": "AD Robot"}])
        )
        mock.get("/traffic_sources").mock(
            return_value=httpx.Response(200, json=[{"id": 2, "name": "Facebook"}])
        )
        mock.get("/domains").mock(
            return_value=httpx.Response(200, json=[{"id": 4, "name": "track.example"}])
        )

        reference = await admin.list_reference_data()

    assert groups.calls[0].request.url.params["type"] == "campaigns", (
        "required and defaulted at once in the schema, which is how a generator writes "
        "'optional' — so it is always sent"
    )
    assert (reference.campaign_groups[0].id, reference.traffic_sources[0].id) == (7, 2)
    assert reference.domains[0].name == "track.example"


async def test_a_refused_catalogue_comes_out_as_the_refusal_and_not_as_a_group_of_one(
    admin: HttpKeitaroAdmin,
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        mock.get("/groups").mock(return_value=httpx.Response(401, json={"error": "denied"}))

        # Not an ExceptionGroup, which is what reading the three at once would produce and
        # what the problem+json handlers of 6.7 would fail to recognise.
        with pytest.raises(UpstreamDeniedError):
            await admin.list_reference_data()


async def test_a_campaign_group_is_created_as_a_campaign_group(admin: HttpKeitaroAdmin) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/groups").mock(
            return_value=httpx.Response(200, json={"id": 7, "name": "AD Robot"})
        )

        created = await admin.create_campaign_group("AD Robot")

    assert _sent(route) == {"name": "AD Robot", "type": "campaigns"}
    assert (created.id, created.name) == (7, "AD Robot")


async def test_a_campaign_is_created_with_the_blueprint_s_own_values(
    admin: HttpKeitaroAdmin,
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/campaigns").mock(
            return_value=httpx.Response(
                200, json={"id": CAMPAIGN_ID, "alias": "summer-mx", "token": "kt-click-token"}
            )
        )

        created = await admin.create_campaign(
            CampaignBlueprint(
                name=CampaignName("Summer MX"),
                alias=CampaignAlias("summer-mx"),
                group_id=7,
                domain_id=4,
            )
        )

    assert _sent(route)["group_id"] == "7", "a string on the write, an integer on the read"
    assert (created.id, created.alias) == (CAMPAIGN_ID, "summer-mx")
    assert "kt-click-token" not in repr(created)


async def test_a_campaign_is_read_by_its_tracker_id(admin: HttpKeitaroAdmin) -> None:
    async with respx.mock(base_url=BASE) as mock:
        mock.get(f"/campaigns/{CAMPAIGN_ID}").mock(
            return_value=httpx.Response(200, json={"id": CAMPAIGN_ID, "name": "Made by hand"})
        )

        assert (await admin.get_campaign(CAMPAIGN_ID)).name == "Made by hand"


async def test_a_flow_is_created_from_a_specification(admin: HttpKeitaroAdmin) -> None:
    spec = StreamSpec(
        campaign_id=CAMPAIGN_ID,
        name="Flow 1",
        type=StreamType.REGULAR,
        schema=StreamSchema.REDIRECT,
        action_type="http",
        position=1,
        action_payload="https://google.com",
    )
    async with respx.mock(base_url=BASE) as mock:
        route = mock.post("/streams").mock(return_value=httpx.Response(200, json=_flow()))

        await admin.create_stream(spec)

    body = _sent(route)
    assert (body["name"], body["schema"], body["position"]) == ("Flow 1", "redirect", 1)
    assert body["action_payload"] == "https://google.com", (
        "no request schema declares this field, and a redirect flow is nothing without it"
    )


async def test_every_flow_of_a_campaign_is_read_in_one_call(admin: HttpKeitaroAdmin) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.get(f"/campaigns/{CAMPAIGN_ID}/streams").mock(
            return_value=httpx.Response(200, json=[_flow(_row(3749, 100))])
        )

        flows = await admin.list_campaign_streams(CAMPAIGN_ID)

    assert route.call_count == 1, "one read draws the whole editor screen"
    assert flows[0].offers[0].offer_id == 3749


async def test_the_whole_offer_catalogue_comes_back_at_once(admin: HttpKeitaroAdmin) -> None:
    async with respx.mock(base_url=BASE) as mock:
        route = mock.get("/offers").mock(
            return_value=httpx.Response(200, json=[{"id": 11112, "name": "Oxys"}])
        )

        offers = await admin.list_offers()

    assert not route.calls[0].request.url.params, "this endpoint takes nothing at all"
    assert offers[0].name == "Oxys"


async def test_a_push_reads_writes_and_reads_again(admin: HttpKeitaroAdmin) -> None:
    async with respx.mock(base_url=BASE) as mock:
        read = mock.get(f"/streams/{STREAM_ID}").mock(
            side_effect=[
                httpx.Response(200, json=_flow(_row(3749, 100))),
                httpx.Response(200, json=_flow(_row(3749, 50), _row(11112, 50))),
            ]
        )
        written = mock.put(f"/streams/{STREAM_ID}").mock(
            return_value=httpx.Response(200, json=_flow(_row(3749, 50), _row(11112, 50)))
        )

        flow = await admin.replace_stream_offers(
            STREAM_ID, (_desired(3749, 50), _desired(11112, 50))
        )

    assert read.call_count == 2, (
        "the second read is the verification: a tracker that merges arrays would echo the "
        "write back unchanged while holding something else"
    )
    assert written.call_count == 1
    assert {row.offer_id: row.share for row in flow.offers} == {3749: 50, 11112: 50}


async def test_a_push_carries_the_geo_filter_it_never_touched(admin: HttpKeitaroAdmin) -> None:
    async with respx.mock(base_url=BASE) as mock:
        mock.get(f"/streams/{STREAM_ID}").mock(
            return_value=httpx.Response(200, json=_flow(_row(3749, 100)))
        )
        written = mock.put(f"/streams/{STREAM_ID}").mock(
            return_value=httpx.Response(200, json=_flow(_row(3749, 100)))
        )

        await admin.replace_stream_offers(STREAM_ID, (_desired(3749, 100),))

    body = _sent(written)
    assert body["filters"] == [{"id": 3, "name": "country", "mode": "accept", "payload": ["AU"]}], (
        "on a build whose PUT replaces, a body without this drops the campaign's geo "
        "targeting — and nobody would blame the button they pressed"
    )
    assert (body["schema"], body["action_type"]) == ("landings", "http"), (
        "resent on every update, because nothing promises a PUT is partial"
    )


async def test_a_removed_offer_travels_explicitly(admin: HttpKeitaroAdmin) -> None:
    async with respx.mock(base_url=BASE) as mock:
        mock.get(f"/streams/{STREAM_ID}").mock(
            side_effect=[
                httpx.Response(200, json=_flow(_row(3749, 50), _row(11112, 50))),
                httpx.Response(200, json=_flow(_row(3749, 100), _row(11112, 0, "disabled"))),
            ]
        )
        written = mock.put(f"/streams/{STREAM_ID}").mock(
            return_value=httpx.Response(200, json=_flow())
        )

        await admin.replace_stream_offers(
            STREAM_ID, (_desired(3749, 100), _desired(11112, 0, OfferState.DISABLED))
        )

    assert {"offer_id": 11112, "share": 0, "state": "disabled"} in _sent(written)["offers"], (
        "correct whether the tracker replaces the array or merges into it"
    )


async def test_a_tracker_that_dropped_the_removed_row_entirely_is_also_right(
    admin: HttpKeitaroAdmin,
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        mock.get(f"/streams/{STREAM_ID}").mock(
            side_effect=[
                httpx.Response(200, json=_flow(_row(3749, 50), _row(11112, 50))),
                # A build whose PUT replaces the array keeps no row for the dropped offer.
                httpx.Response(200, json=_flow(_row(3749, 100))),
            ]
        )
        mock.put(f"/streams/{STREAM_ID}").mock(return_value=httpx.Response(200, json=_flow()))

        flow = await admin.replace_stream_offers(
            STREAM_ID, (_desired(3749, 100), _desired(11112, 0, OfferState.DISABLED))
        )

    assert [row.offer_id for row in flow.offers] == [3749]


async def test_a_push_the_tracker_did_not_take_is_raised_with_the_numbers(
    admin: HttpKeitaroAdmin,
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        mock.get(f"/streams/{STREAM_ID}").mock(
            side_effect=[
                httpx.Response(200, json=_flow(_row(3749, 100))),
                # The tracker normalised the shares back to its own idea of them.
                httpx.Response(200, json=_flow(_row(3749, 33), _row(11112, 67))),
            ]
        )
        mock.put(f"/streams/{STREAM_ID}").mock(return_value=httpx.Response(200, json=_flow()))

        with pytest.raises(UpstreamProtocolError) as raised:
            await admin.replace_stream_offers(STREAM_ID, (_desired(3749, 34), _desired(11112, 66)))

    assert "asked for 34% active" in str(raised.value)
    assert "holds 33% active" in str(raised.value)


async def test_a_row_the_push_never_mentioned_is_a_disagreement_too(
    admin: HttpKeitaroAdmin,
) -> None:
    async with respx.mock(base_url=BASE) as mock:
        mock.get(f"/streams/{STREAM_ID}").mock(
            side_effect=[
                httpx.Response(200, json=_flow(_row(3749, 100))),
                # Somebody added an offer in Keitaro between the write and the read.
                httpx.Response(200, json=_flow(_row(3749, 100), _row(22222, 50))),
            ]
        )
        mock.put(f"/streams/{STREAM_ID}").mock(return_value=httpx.Response(200, json=_flow()))

        with pytest.raises(UpstreamProtocolError, match="did not ask for"):
            await admin.replace_stream_offers(STREAM_ID, (_desired(3749, 100),))


async def test_a_row_asked_for_and_missing_is_named(admin: HttpKeitaroAdmin) -> None:
    async with respx.mock(base_url=BASE) as mock:
        mock.get(f"/streams/{STREAM_ID}").mock(
            side_effect=[
                httpx.Response(200, json=_flow(_row(3749, 100))),
                httpx.Response(200, json=_flow(_row(3749, 100))),
            ]
        )
        mock.put(f"/streams/{STREAM_ID}").mock(return_value=httpx.Response(200, json=_flow()))

        with pytest.raises(UpstreamProtocolError, match="holds no row at all"):
            await admin.replace_stream_offers(STREAM_ID, (_desired(3749, 50), _desired(11112, 50)))


async def test_a_removed_row_the_tracker_left_running_is_named(admin: HttpKeitaroAdmin) -> None:
    async with respx.mock(base_url=BASE) as mock:
        mock.get(f"/streams/{STREAM_ID}").mock(
            side_effect=[
                httpx.Response(200, json=_flow(_row(3749, 50), _row(11112, 50))),
                httpx.Response(200, json=_flow(_row(3749, 50), _row(11112, 50))),
            ]
        )
        mock.put(f"/streams/{STREAM_ID}").mock(return_value=httpx.Response(200, json=_flow()))

        with pytest.raises(UpstreamProtocolError, match="switched off"):
            await admin.replace_stream_offers(
                STREAM_ID, (_desired(3749, 50), _desired(11112, 0, OfferState.DISABLED))
            )


async def test_a_flow_written_back_wholesale_keeps_its_own_identity(
    admin: HttpKeitaroAdmin,
) -> None:
    spec = StreamSpec(
        campaign_id=CAMPAIGN_ID,
        name="Flow 2",
        type=StreamType.REGULAR,
        schema=StreamSchema.LANDINGS,
        action_type="http",
    )
    async with respx.mock(base_url=BASE) as mock:
        route = mock.put(f"/streams/{STREAM_ID}").mock(
            return_value=httpx.Response(200, json=_flow())
        )

        await admin.update_stream(STREAM_ID, spec)

    assert _sent(route)["campaign_id"] == CAMPAIGN_ID
