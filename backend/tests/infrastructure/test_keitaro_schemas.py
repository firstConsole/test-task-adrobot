"""What the wire models do with the payloads the published schema gets wrong.

Every test here is one row of `docs/keitaro-api-notes.md` §2 made executable. The models
are the only place those defects are handled, so this is where a newer tracker build that
fixed one — or introduced another — shows up.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adrobot.infrastructure.keitaro.schemas import (
    KtCampaign,
    KtCampaignCreate,
    KtFilterWrite,
    KtOffer,
    KtOfferWrite,
    KtRange,
    KtReport,
    KtStream,
    KtStreamWrite,
)

A_FLOW = {
    "id": 564221,
    "campaign_id": 93212,
    "name": "Flow 2",
    "type": "regular",
    "schema": "landings",
    "action_type": "http",
    "collect_clicks": True,
    "filters": [{"id": 3, "name": "country", "mode": "accept", "payload": ["AU"]}],
    "offers": [{"id": 11, "stream_id": 564221, "offer_id": 3749, "share": 100, "state": "active"}],
}


def _written_flow(**overrides: object) -> KtStreamWrite:
    return KtStreamWrite(
        campaign_id=93212,
        name="Flow 2",
        type="regular",
        flow_schema="landings",
        action_type="http",
        **overrides,  # type: ignore[arg-type]  # one kwarg per field, checked by the model
    )


def test_a_campaign_read_back_drops_the_click_api_token() -> None:
    campaign = KtCampaign.model_validate({"id": 93212, "name": "Demo", "token": "kt-click-token"})

    assert "token" not in campaign.model_dump()
    assert not hasattr(campaign, "token")


def test_a_read_survives_a_field_the_tracker_added_since() -> None:
    flow = KtStream.model_validate({**A_FLOW, "offer_selection": "after_click", "invented": 1})

    assert flow.id == 564221


def test_a_filter_payload_reads_the_same_whether_it_arrives_as_an_array_or_a_string() -> None:
    # §2: Filter.payload is typed `string` on read and `array` on write. Both shapes are
    # the same two countries, and a mapper that believed either one alone would break.
    as_array = KtStream.model_validate(
        {**A_FLOW, "filters": [{"name": "geo", "payload": ["AU", "NZ"]}]}
    )
    as_string = KtStream.model_validate(
        {**A_FLOW, "filters": [{"name": "geo", "payload": "AU, NZ"}]}
    )

    assert as_array.filters[0].payload == as_string.filters[0].payload == ("AU", "NZ")


def test_an_offer_with_no_countries_at_all_still_reads() -> None:
    assert KtOffer.model_validate({"id": 11112, "country": None}).country == ()


def test_the_flow_schema_keeps_its_wire_spelling_in_both_directions() -> None:
    # A pydantic field actually named `schema` warns at class-definition time, which this
    # suite's filterwarnings turns into an ImportError. The alias is the whole workaround.
    read = KtStream.model_validate(A_FLOW)

    assert read.flow_schema == "landings"
    assert _written_flow().body()["schema"] == "landings"


def test_a_written_range_spells_from_the_way_the_tracker_does() -> None:
    assert KtRange(timezone="Europe/Madrid", from_="2026-09-20", to="2026-09-20").body() == {
        "timezone": "Europe/Madrid",
        "from": "2026-09-20",
        "to": "2026-09-20",
    }


def test_a_write_omits_what_was_never_set_and_keeps_what_was_set_to_false() -> None:
    body = _written_flow(collect_clicks=False).body()

    assert "comments" not in body, "an unset field must not travel as null into a replacement"
    assert body["collect_clicks"] is False, (
        "a flow that does not record clicks is a decision, not a default to drop: the "
        "statistics screen reads exactly these clicks"
    )


def test_a_campaign_is_created_with_its_group_as_a_string() -> None:
    body = KtCampaignCreate(name="Demo", alias="demo-mx", group_id="7").body()

    assert body["group_id"] == "7", "the write takes a string where the read gives an integer"
    assert body["type"] == "position"
    assert "traffic_source_id" not in body


def test_a_removed_offer_is_expressible_at_all() -> None:
    # The shape a push sends a removal in, and the reason it is correct whether the
    # tracker replaces the offers array or merges into it.
    assert KtOfferWrite(offer_id=3749, share=0, state="disabled").body() == {
        "offer_id": 3749,
        "share": 0,
        "state": "disabled",
    }


def test_a_write_refuses_a_field_name_it_does_not_know() -> None:
    # A misspelling the tracker would ignore silently, which reads as a push that
    # succeeded and changed nothing.
    with pytest.raises(ValidationError, match="mod"):
        KtFilterWrite(name="country", mode="accept", mod="accept")  # type: ignore[call-arg]


def test_a_report_is_read_as_rows_of_objects_whatever_the_schema_says() -> None:
    report = KtReport.model_validate(
        {"rows": [{"stream_id": 564221, "clicks": 7}], "total": 1, "meta": []}
    )

    assert report.rows == ({"stream_id": 564221, "clicks": 7},)
