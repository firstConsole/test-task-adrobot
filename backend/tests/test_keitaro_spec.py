"""The claims `docs/keitaro-api-notes.md` §1 and §2 make about the downloaded schema.

The notes are the artefact of stage 2, and a document that quietly stops being true is
worse than no document: everything written against Keitaro is written from it. So every
row of those two sections is asserted here against `docs/keitaro-openapi.json` itself. A
newer download of the schema that changes one of them turns this red, with the row named,
instead of leaving the notes to age in place.

The traps in §2 are asserted exactly like the facts in §1 — a defect that quietly gets
fixed upstream is news too, and the project's own workaround can then go.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

SPEC_PATH: Final = Path(__file__).resolve().parents[2] / "docs" / "keitaro-openapi.json"

PATH_COUNT: Final = 87
SCHEMA_COUNT: Final = 69
CREATES: Final = ("/campaigns", "/streams", "/groups")


@lru_cache(maxsize=1)
def _spec() -> dict[str, Any]:
    loaded = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _at(*keys: str) -> dict[str, Any]:
    """Return a mapping out of the spec by path, so a test reads as its own claim."""
    node: Any = _spec()
    for key in keys:
        node = node[key]
    assert isinstance(node, dict)
    return node


def _schema(name: str) -> dict[str, Any]:
    return _at("components", "schemas", name)


def _properties(name: str) -> dict[str, Any]:
    return _at("components", "schemas", name, "properties")


def test_the_schema_is_the_one_the_notes_describe() -> None:
    # Without this, a missing or replaced file would make every assertion below vacuous.
    assert SPEC_PATH.is_file(), f"{SPEC_PATH} is the source of truth for the notes"
    assert _spec()["info"]["title"] == "Keitaro Admin API"
    assert _spec()["servers"][0]["url"] == "/admin_api/v1"
    assert (len(_at("paths")), len(_at("components", "schemas"))) == (PATH_COUNT, SCHEMA_COUNT)

    auth = _at("components", "securitySchemes", "ApiKeyAuth")
    assert (auth["in"], auth["name"]) == ("header", "Api-Key"), (
        "the key travels in a custom header, which is why the transport refuses redirects: "
        "httpx strips Authorization across hosts and would carry this one"
    )


def test_creating_anything_answers_200_and_declares_406() -> None:
    for path in CREATES:
        codes = set(_at("paths", path, "post", "responses"))
        assert "200" in codes, f"POST {path} answers {sorted(codes)}"
        assert "201" not in codes, f"POST {path} answers {sorted(codes)}"
        assert "406" in codes, f"POST {path} reports a validation failure as 406, not 400"


def test_a_flow_is_updated_with_put_which_declares_404_and_no_406() -> None:
    flow = _at("paths", "/streams/{id}")
    assert "put" in flow
    assert "post" not in flow, "there is no POST /streams/{id} to fall back on"
    assert set(_at("paths", "/streams/{id}", "put", "responses")) == {
        "200",
        "400",
        "401",
        "402",
        "404",
        "500",
    }


def test_the_offer_catalogue_cannot_be_searched_on_the_server() -> None:
    assert "parameters" not in _at("paths", "/offers", "get"), (
        "GET /offers takes nothing at all, which is why the catalogue is mirrored locally "
        "and the autocomplete searches the mirror"
    )
    assert [p["name"] for p in _at("paths", "/campaigns", "get")["parameters"]] == [
        "offset",
        "limit",
    ], "campaigns are paginated although offers are not — the asymmetry is the tracker's"


def test_a_campaign_is_created_from_an_alias_and_a_name() -> None:
    assert sorted(_schema("CampaignCreateRequired")["required"]) == ["alias", "name"]
    assert _properties("CampaignRequest")["cost_type"]["enum"] == ["CPC", "CPUC", "CPM"]
    assert len(_properties("Campaign")["cost_type"]["enum"]) > 3, "read is wider than write"
    assert _properties("CampaignRequest")["group_id"]["type"] == "string"
    assert _properties("Campaign")["group_id"]["type"] == "integer", (
        "a campaign takes its group as a string and gives it back as an integer"
    )
    assert "token" in _properties("Campaign"), (
        "the Click API token is inside the campaign object, which is why no 2xx body is "
        "ever logged and a non-2xx one goes through adrobot.logging.redact"
    )


def test_an_offer_inside_a_flow_is_integers_all_the_way() -> None:
    request = _schema("OfferStreamRequest")
    assert sorted(request["required"]) == ["offer_id", "share"]
    assert request["properties"]["share"]["type"] == "integer"
    assert _properties("OfferStream")["share"]["type"] == "integer", (
        "there are no fractional shares in this API, which is what makes divmod right "
        "rather than approximately right"
    )
    assert request["properties"]["state"]["enum"] == ["active", "disabled"], (
        "a removal can be expressed, which is what the push relies on"
    )
    assert {"id", "created_at"} <= set(_properties("OfferStream")), (
        "the tie-break rule orders the tracker's own rows by stream_offer.created_at"
    )


def test_the_two_filter_schemas_are_not_the_same_schema() -> None:
    flow = _schema("FilterStreamRequest")
    report = _schema("FilterRequest")
    assert sorted(flow["required"]) == ["mode", "name"]
    assert flow["properties"]["payload"]["type"] == "array"
    assert sorted(report["required"]) == ["name", "operator"]
    assert set(report["properties"]) == {"name", "operator", "expression"}
    assert _properties("Filter")["payload"]["type"] == "string", (
        "§2: a flow filter's payload is written as an array and typed on read as a string; "
        "the wire model has to survive both"
    )


def test_the_write_schema_of_a_flow_omits_the_payload_a_redirect_needs() -> None:
    assert "action_payload" not in _properties("StreamObject"), (
        "§2: the field a redirect flow is made of is in no request schema"
    )
    assert "oneOf" in _properties("Stream")["action_payload"], "on read it is string or object"
    assert sorted(_schema("StreamRequest")["allOf"][1]["required"]) == sorted(
        ["campaign_id", "schema", "type", "name", "action_type"]
    )
    assert "required" not in _schema("StreamRequestPut"), (
        "§2: the update schema is a bare $ref with nothing required, so nothing promises "
        "that a PUT is partial — every PUT resends action_type and schema"
    )
    assert _properties("StreamObject")["schema"]["enum"] == ["landings", "redirect", "action"]
    assert sorted(_properties("StreamObject")["type"]["enum"]) == ["default", "forced", "regular"]


def test_a_report_is_built_from_a_range_dimensions_and_measures() -> None:
    assert set(_properties("ReportsRequest")) == {
        "range",
        "dimensions",
        "measures",
        "filters",
        "sort",
    }
    assert set(_properties("RangeRequest")) == {"from", "to", "timezone", "interval"}
    assert _properties("Report")["rows"]["items"]["type"] == "string", (
        "§2: report rows are objects; the schema types them as strings, so the row shape "
        "comes from the probe and not from here"
    )


def test_deleting_a_campaign_is_an_archive_that_answers_201() -> None:
    delete = _at("paths", "/campaigns/{id}", "delete")
    assert delete["summary"] == "Move campaign to archive"
    assert set(delete["responses"]) == {"201", "400", "401", "402", "500"}, (
        "§2: the one endpoint in this API whose success code is 201, and it is a delete"
    )
    assert "Domain" in json.dumps(delete["responses"]["201"]), (
        "§2: it declares the body of another resource entirely"
    )
    assert "/campaigns/clean_archive" in _at("paths"), "the archive is emptied separately"


def test_a_campaign_cannot_be_asked_which_domain_it_was_created_on() -> None:
    assert "domain_id" in _properties("CampaignRequest")
    assert "domain_id" not in _properties("Campaign"), (
        "§2: the domain is write-only, so a campaign's public link is built from what was "
        "sent and cannot be rebuilt by reading the campaign back"
    )


def test_two_flow_fields_are_readable_and_cannot_be_written_back() -> None:
    assert "offer_selection" in _properties("Stream")
    assert "offer_selection" not in _properties("StreamObject"), (
        "§2: a replacing PUT would reset a setting nothing is able to resend"
    )
    assert "taget" in _properties("Trigger"), "§2: the read schema misspells `target`"
    assert "target" not in _properties("Trigger")
    assert "target" in _schema("TriggersStreamRequest")["required"], (
        "§2: read and write disagree about the field's name, so a trigger cannot make the "
        "round trip and this project does not model one"
    )


def test_the_action_catalogue_is_spelled_with_an_s() -> None:
    assert "/streams_actions" in _at("paths")
    assert "/stream_actions" not in _at("paths"), (
        "§2: two published clients use the singular; only one of the two spellings answers"
    )


def test_the_group_type_parameter_is_required_and_defaulted_at_once() -> None:
    parameter = _at("paths", "/groups", "get")["parameters"][0]
    assert parameter["name"] == "type"
    assert parameter["required"] is True
    assert parameter["schema"]["default"] == "campaigns", (
        "§2: required and defaulted together, which is how a generator writes 'optional'"
    )
