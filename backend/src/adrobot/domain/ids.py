"""Identifier types.

`NewType` rather than a wrapper class: these are carried, never operated on, and the two
integer families — ours and Keitaro's — are the pair that must not be mixed up. mypy
rejects a `KeitaroStreamId` where a `KeitaroCampaignId` is expected; `int` would not.
"""

from __future__ import annotations

from typing import NewType
from uuid import UUID

CampaignId = NewType("CampaignId", UUID)
DraftId = NewType("DraftId", UUID)

KeitaroCampaignId = NewType("KeitaroCampaignId", int)
KeitaroStreamId = NewType("KeitaroStreamId", int)
OfferId = NewType("OfferId", int)
