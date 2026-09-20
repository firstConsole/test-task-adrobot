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
# Handed from push phase 1 to phase 3, across an HTTP call, beside a DraftId that is also a
# UUID — which is the mix-up this module exists to make mypy reject.
PushAttemptId = NewType("PushAttemptId", UUID)

KeitaroCampaignId = NewType("KeitaroCampaignId", int)
KeitaroStreamId = NewType("KeitaroStreamId", int)
OfferId = NewType("OfferId", int)
