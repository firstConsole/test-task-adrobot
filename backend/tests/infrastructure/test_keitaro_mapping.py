"""The border crossing: what the tracker's payloads become, and what never comes back out.

Three of these are about a body that is wrong rather than one that is ordinary, because
that is where a mapper decides whether the next screen shows a clear failure or an empty
table. The last one is about a secret: the message of a `ValidationError` quotes what it
could not validate, and here that is a campaign object.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from adrobot.application.errors import UpstreamProtocolError
from adrobot.domain.campaign import CampaignBlueprint, CampaignRotation
from adrobot.domain.diff import DesiredOffer
from adrobot.domain.ids import KeitaroCampaignId, OfferId
from adrobot.domain.stream import (
    FilterMode,
    StreamFilter,
    StreamLanding,
    StreamSchema,
    StreamSpec,
    StreamType,
)
from adrobot.domain.values import CampaignAlias, CampaignName, OfferState
from adrobot.infrastructure.keitaro.mapping import (
    campaign_body,
    group_body,
    stream_body,
    to_campaign,
    to_offers,
    to_reference_data,
    to_stream,
    to_streams,
)

MADRID = ZoneInfo("Europe/Madrid")

A_TOKEN = "kt-click-api-token-93212"
A_CAMPAIGN = {
    "id": 93212,
    "alias": "SummerMX",
    "name": "Summer MX",
    "state": "active",
    "group_id": 7,
    "token": A_TOKEN,
    "created_at": "2026-09-20 11:30:00",
}
A_FLOW = {
    "id": 564221,
    "campaign_id": 93212,
    "name": "Flow 2",
    "type": "regular",
    "schema": "landings",
    "action_type": "http",
    "collect_clicks": True,
    "filters": [{"id": 3, "name": "country", "mode": "accept", "payload": ["AU"]}],
    "landings": [{"id": 5, "landing_id": 42, "share": 100, "state": "active"}],
    "offers": [
        {
            "id": 11,
            "stream_id": 564221,
            "offer_id": 3749,
            "share": 100,
            "state": "active",
            "created_at": "2026-09-20 11:31:00",
        }
    ],
}


def _spec(**overrides: object) -> StreamSpec:
    fields: dict[str, object] = {
        "campaign_id": KeitaroCampaignId(93212),
        "name": "Flow 2",
        "type": StreamType.REGULAR,
        "schema": StreamSchema.LANDINGS,
        "action_type": "http",
        **overrides,
    }
    return StreamSpec(**fields)  # type: ignore[arg-type]  # one key per field, checked below


def test_a_campaign_arrives_without_the_token_it_was_sent_with() -> None:
    campaign = to_campaign(A_CAMPAIGN, zone=MADRID)

    assert (campaign.id, campaign.name, campaign.group_id) == (93212, "Summer MX", 7)
    assert A_TOKEN not in repr(campaign)


def test_an_alias_somebody_typed_by_hand_is_read_as_it_stands() -> None:
    # `SummerMX` would not pass CampaignAlias. Campaign 93212 from the video was made by
    # hand, and part 2 is the editor for campaigns exactly like it.
    assert to_campaign(A_CAMPAIGN, zone=MADRID).alias == "SummerMX"


def test_a_timestamp_without_an_offset_is_read_in_the_tracker_s_own_zone() -> None:
    created = to_campaign(A_CAMPAIGN, zone=MADRID).created_at

    assert created == datetime(2026, 9, 20, 11, 30, tzinfo=MADRID)
    assert created != datetime(2026, 9, 20, 11, 30, tzinfo=UTC), (
        "reading these as UTC is what moves the boundary of 'clicks today' by two hours"
    )


def test_a_timestamp_the_tracker_mangled_costs_the_row_its_order_and_nothing_more() -> None:
    flow = to_stream({**A_FLOW, "offers": [{"offer_id": 3749, "created_at": "soon"}]}, zone=MADRID)

    assert flow.offers[0].created_at is None
    assert flow.offers[0].offer_id == 3749


def test_a_row_with_no_timestamp_at_all_is_read_without_one() -> None:
    flow = to_stream({**A_FLOW, "offers": [{"offer_id": 3749, "share": 100}]}, zone=MADRID)

    assert flow.offers[0].created_at is None


def test_a_timestamp_that_already_carries_an_offset_keeps_it() -> None:
    # Not the shape this build sends, and the one that must not be shifted twice if a
    # later one does.
    stamped = {**A_FLOW, "offers": [{"offer_id": 3749, "created_at": "2026-09-20T11:30:00+00:00"}]}

    assert to_stream(stamped, zone=MADRID).offers[0].created_at == datetime(
        2026, 9, 20, 11, 30, tzinfo=UTC
    )


def test_a_flow_arrives_whole() -> None:
    flow = to_stream(A_FLOW, zone=MADRID)

    assert flow.schema is StreamSchema.LANDINGS
    assert flow.type is StreamType.REGULAR
    assert flow.filters == (StreamFilter(name="country", mode="accept", payload=("AU",), id=3),)
    assert flow.landings == (StreamLanding(landing_id=42, share=100, state="active"),)
    assert (flow.offers[0].offer_id, flow.offers[0].share, flow.offers[0].row_id) == (3749, 100, 11)


def test_a_filter_mode_nobody_has_heard_of_is_carried_rather_than_refused() -> None:
    odd = {**A_FLOW, "filters": [{"name": "country", "mode": "accept_new", "payload": ["AU"]}]}

    assert to_stream(odd, zone=MADRID).filters[0].mode == "accept_new"


@pytest.mark.parametrize("field", ["schema", "type"])
def test_a_flow_this_service_cannot_describe_is_refused_by_name(field: str) -> None:
    with pytest.raises(UpstreamProtocolError, match="564221"):
        to_stream({**A_FLOW, field: "something-new"}, zone=MADRID)


def test_a_listing_that_is_not_a_list_is_the_tracker_s_fault_and_not_an_empty_screen() -> None:
    with pytest.raises(UpstreamProtocolError, match="not as an array"):
        to_streams({"rows": [A_FLOW]}, zone=MADRID)


def test_a_body_that_cannot_be_read_never_quotes_itself_back() -> None:
    # pydantic's own message renders `input_value=` for every failing field, and the input
    # here is a campaign object with a Click API token in it.
    with pytest.raises(UpstreamProtocolError) as raised:
        to_campaign({**A_CAMPAIGN, "id": "not an id"}, zone=MADRID)

    assert "id:" in str(raised.value), "it still says which field it choked on"
    assert A_TOKEN not in str(raised.value)
    assert "not an id" not in str(raised.value)


def test_the_three_catalogues_are_read_together() -> None:
    reference = to_reference_data(
        groups=[{"id": 7, "name": "AD Robot"}],
        sources=[{"id": 2, "name": "Facebook", "state": "active"}],
        domains=[{"id": 4, "name": "track.example", "state": "active"}],
    )

    assert reference.campaign_groups[0].name == "AD Robot"
    assert reference.traffic_sources[0].id == 2
    assert reference.domains[0].name == "track.example"


def test_an_offer_keeps_the_relative_preview_path_the_tracker_gave_it() -> None:
    offers = to_offers([{"id": 11112, "name": "Oxys", "preview_path": "/preview/11112"}])

    assert offers[0].preview_path == "/preview/11112", (
        "joining it onto the public base is the API layer's job, not this one's"
    )


def test_a_campaign_is_written_with_its_group_as_a_string() -> None:
    body = campaign_body(
        CampaignBlueprint(
            name=CampaignName("Summer MX"),
            alias=CampaignAlias("summer-mx"),
            group_id=7,
            domain_id=4,
        )
    )

    assert body["group_id"] == "7"
    assert body["type"] == CampaignRotation.POSITION.value
    assert (body["name"], body["alias"], body["domain_id"]) == ("Summer MX", "summer-mx", 4)
    assert "traffic_source_id" not in body, "unset, so it does not travel as null"


def test_a_group_is_created_as_a_campaign_group() -> None:
    assert group_body("AD Robot") == {"name": "AD Robot", "type": "campaigns"}


def test_a_flow_is_written_with_every_field_it_was_read_with() -> None:
    written = stream_body(
        _spec(
            filters=(StreamFilter(name="country", mode=FilterMode.ACCEPT, payload=("MX",), id=3),),
            landings=(StreamLanding(landing_id=42, share=100),),
            offers=(
                DesiredOffer(offer_id=OfferId(3749), share=0, state=OfferState.DISABLED),
                DesiredOffer(offer_id=OfferId(11112), share=100, state=OfferState.ACTIVE),
            ),
            action_payload="https://google.com",
        )
    )

    assert written["schema"] == "landings"
    assert written["filters"] == [{"name": "country", "mode": "accept", "payload": ["MX"], "id": 3}]
    assert written["landings"] == [{"landing_id": 42, "share": 100, "state": "active"}]
    assert written["offers"] == [
        {"offer_id": 3749, "share": 0, "state": "disabled"},
        {"offer_id": 11112, "share": 100, "state": "active"},
    ]
    assert written["action_payload"] == "https://google.com"


def test_a_structured_action_payload_survives_the_round_trip() -> None:
    # `string | object` on the wire. A push that dropped the object half would reset it on
    # a tracker whose PUT replaces rather than merges.
    flow = to_stream({**A_FLOW, "action_payload": {"url": "https://google.com"}}, zone=MADRID)

    assert stream_body(flow.respecified(()))["action_payload"] == {"url": "https://google.com"}


def test_a_flow_read_and_written_back_keeps_what_the_editor_never_touches() -> None:
    flow = to_stream({**A_FLOW, "comments": "made by hand", "filter_or": True}, zone=MADRID)

    written = stream_body(
        flow.respecified(
            (DesiredOffer(offer_id=OfferId(3749), share=100, state=OfferState.ACTIVE),)
        )
    )

    assert written["comments"] == "made by hand"
    assert written["filter_or"] is True
    assert written["filters"][0]["payload"] == ["AU"], (
        "the campaign's geo targeting, which a push that forgot it would silently drop"
    )
    assert written["landings"] == [{"landing_id": 42, "share": 100, "state": "active"}]
