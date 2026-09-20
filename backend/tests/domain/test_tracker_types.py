"""The rules the Keitaro-facing domain types carry, which are mostly rules about absences.

A type is a poor place for a comment and a good place for a test: three of the decisions in
`campaign.py`, `offer.py` and `stream.py` are fields that are deliberately *not* there, or
fields that must all be there together, and both kinds are invisible on reading the code
back a month later.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from adrobot.domain.campaign import Campaign, CampaignBlueprint, CampaignRotation, CostType
from adrobot.domain.diff import DesiredOffer
from adrobot.domain.errors import DuplicateOfferRowError
from adrobot.domain.ids import KeitaroCampaignId, KeitaroStreamId, OfferId
from adrobot.domain.offer import Offer
from adrobot.domain.stream import (
    FilterMode,
    Stream,
    StreamFilter,
    StreamLanding,
    StreamOffer,
    StreamSchema,
    StreamSpec,
    StreamType,
)
from adrobot.domain.values import CampaignAlias, CampaignName, OfferState


def _desired(offer_id: int, share: int) -> DesiredOffer:
    return DesiredOffer(offer_id=OfferId(offer_id), share=share, state=OfferState.ACTIVE)


def test_a_campaign_does_not_carry_the_click_api_token() -> None:
    # Keitaro returns the token inside the campaign object. A field here would put it in
    # the mirror, in an API response and — the day something logs a campaign — in a log.
    assert "token" not in {field.name for field in fields(Campaign)}


def test_a_campaign_does_not_carry_a_domain_it_cannot_be_asked_for() -> None:
    # CampaignRequest takes domain_id on the write; Campaign declares none on the read.
    # The public link is built from what we sent, so an empty field here would be a lie.
    assert "domain_id" not in {field.name for field in fields(Campaign)}
    assert "domain_id" in {field.name for field in fields(CampaignBlueprint)}


def test_a_blueprint_defaults_to_what_part_one_builds() -> None:
    blueprint = CampaignBlueprint(
        name=CampaignName("Summer MX"), alias=CampaignAlias("summer-mx"), group_id=7
    )

    assert blueprint.rotation is CampaignRotation.POSITION
    assert blueprint.cost_type is CostType.CPC
    assert blueprint.cookies_ttl == 24


def test_an_offer_keeps_the_catalogue_exactly_as_the_tracker_spells_it() -> None:
    # `[pl es -]` is what the reference tool prints for a real offer: a dash is one of the
    # values Keitaro keeps in that array, and CountryCode would refuse the whole catalogue.
    offer = Offer(id=OfferId(11112), name="Oxys", state="active", country=("pl", "es", "-"))

    assert offer.country == ("pl", "es", "-")


def test_a_flow_read_from_the_tracker_keeps_shares_that_do_not_add_up() -> None:
    # The clean state in the video sums to 50%. Normalising it would be inventing data.
    rows = (
        StreamOffer(offer_id=OfferId(3749), share=25, state="active"),
        StreamOffer(offer_id=OfferId(3717), share=25, state="active"),
    )

    assert sum(row.share for row in rows) == 50


def test_the_two_stream_types_carry_the_same_fields() -> None:
    """A push writes the flow whole, so a field on one and not the other is data loss.

    Not decoration: the reference campaign's Flow 2 carries `country accept ["AU"]` and is
    the flow the editor pushes to. If `filters` were readable and not writable, pressing
    PUSH TO KT would drop the campaign's geo targeting.
    """
    read = {field.name for field in fields(Stream)}
    written = {field.name for field in fields(StreamSpec)}

    assert read - written == {"id"}, "a field the tracker gives back and a push cannot resend"
    assert written - read == set(), "a field a push sends that nothing ever reads back"


def test_respecifying_a_flow_changes_the_offers_and_nothing_else() -> None:
    flow = Stream(
        id=KeitaroStreamId(564221),
        campaign_id=KeitaroCampaignId(93212),
        name="Flow 2",
        type=StreamType.REGULAR,
        schema=StreamSchema.LANDINGS,
        action_type="http",
        position=2,
        weight=1.0,
        state="active",
        action_payload="https://google.com",
        collect_clicks=True,
        filter_or=True,
        comments="left by whoever made this campaign by hand",
        filters=(StreamFilter(name="country", mode=FilterMode.ACCEPT, payload=("AU",), id=11),),
        landings=(StreamLanding(landing_id=42, share=100),),
        offers=(StreamOffer(offer_id=OfferId(3749), share=100, state="active"),),
    )

    spec = flow.respecified((_desired(3749, 50), _desired(11112, 50)))

    assert spec.offers == (_desired(3749, 50), _desired(11112, 50))
    carried = {field.name for field in fields(StreamSpec)} - {"offers"}
    assert {name: getattr(spec, name) for name in carried} == {
        name: getattr(flow, name) for name in carried
    }


def test_a_specification_refuses_to_name_one_offer_twice() -> None:
    with pytest.raises(DuplicateOfferRowError, match="3749"):
        StreamSpec(
            campaign_id=KeitaroCampaignId(93212),
            name="Flow 2",
            type=StreamType.REGULAR,
            schema=StreamSchema.LANDINGS,
            action_type="http",
            offers=(_desired(3749, 50), _desired(3749, 50)),
        )
