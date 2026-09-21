"""What two requests do to one flow, and what the push's own bookkeeping refuses.

Everything here needs two real transactions, so these run on `pooled_sessions` — commits
that another connection can see — and sweep after themselves. Every wait is bounded: a
regression here would otherwise hang the suite rather than fail it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from adrobot.application.errors import DraftAlreadyOpenError, DraftStatusChangedError
from adrobot.application.ports.persistence import CampaignSetup
from adrobot.domain.campaign import Campaign, CampaignSetupStatus
from adrobot.domain.draft import DraftStatus
from adrobot.domain.ids import KeitaroCampaignId, KeitaroStreamId, OfferId
from adrobot.domain.stream import Stream, StreamOffer, StreamSchema, StreamType
from adrobot.infrastructure.db.uow import unit_of_work

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from adrobot.application.ports.persistence import MirroredCampaign

NOW = datetime(2026, 3, 1, 12, tzinfo=UTC)
FLOW = KeitaroStreamId(564221)
HASH = "a" * 64

# Longer than the three seconds `lock_timeout` gives a waiter, so a blocked statement is
# refused by PostgreSQL and reported, and shorter than any patience a human has.
DEADLINE = 6.0


async def given_a_flow(sessions: async_sessionmaker[AsyncSession]) -> MirroredCampaign:
    async with unit_of_work(sessions) as uow, uow.begin() as tx:
        mirrored = await tx.campaigns.add(
            Campaign(id=KeitaroCampaignId(93212), alias="a1b2c3d4", name="Demo", state="active"),
            setup=CampaignSetup(status=CampaignSetupStatus.READY),
        )
        await tx.streams.upsert_campaign_streams(
            campaign_id=mirrored.id,
            at=NOW,
            streams=(
                Stream(
                    id=FLOW,
                    campaign_id=KeitaroCampaignId(93212),
                    name="Flow 1",
                    type=StreamType.REGULAR,
                    schema=StreamSchema.LANDINGS,
                    action_type="http",
                    position=1,
                    offers=(StreamOffer(offer_id=OfferId(3749), share=100, state="active"),),
                ),
            ),
        )
    return mirrored


async def test_a_flow_may_have_only_one_live_draft(
    pooled_sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Two tabs editing one flow. The loser is told so by our own error rather than by an
    # IntegrityError, which is what lets the use case answer 409 instead of 500.
    mirrored = await given_a_flow(pooled_sessions)
    async with unit_of_work(pooled_sessions) as uow:
        async with uow.begin() as tx:
            await tx.drafts.open_for(
                campaign_id=mirrored.id, stream_id=FLOW, rows=(), base_snapshot_hash=HASH
            )
        async with uow.begin() as tx:
            with pytest.raises(DraftAlreadyOpenError):
                await tx.drafts.open_for(
                    campaign_id=mirrored.id,
                    stream_id=FLOW,
                    rows=(),
                    base_snapshot_hash="b" * 64,
                )


async def test_two_pushes_cannot_both_believe_they_won(
    pooled_sessions: async_sessionmaker[AsyncSession],
) -> None:
    # The compare-and-swap that stands behind the row lock: it holds even for a caller that
    # forgot to take one, which is the whole reason the transition is not a read and a write.
    mirrored = await given_a_flow(pooled_sessions)
    async with unit_of_work(pooled_sessions) as uow:
        async with uow.begin() as tx:
            draft = await tx.drafts.open_for(
                campaign_id=mirrored.id, stream_id=FLOW, rows=(), base_snapshot_hash=HASH
            )
        async with uow.begin() as tx:
            await tx.drafts.change_status(
                draft.id, was=DraftStatus.OPEN, becomes=DraftStatus.PUSHING
            )
        async with uow.begin() as tx:
            with pytest.raises(DraftStatusChangedError) as refused:
                await tx.drafts.change_status(
                    draft.id, was=DraftStatus.OPEN, becomes=DraftStatus.PUSHING
                )
    # The status it found, in the API's own words rather than Python's.
    assert "pushing" in str(refused.value)


async def test_the_flow_lock_makes_a_second_writer_wait(
    pooled_sessions: async_sessionmaker[AsyncSession],
) -> None:
    # The lock is what serialises an edit, a pin and a push on one flow. A second writer must
    # not get through while the first holds it — and must get through the moment it lets go.
    mirrored = await given_a_flow(pooled_sessions)
    released = asyncio.Event()

    async def holder() -> None:
        async with unit_of_work(pooled_sessions) as uow, uow.begin() as tx:
            await tx.streams.lock(campaign_id=mirrored.id, stream_id=FLOW)
            await asyncio.sleep(0.3)
            released.set()

    async def second_writer() -> bool:
        async with unit_of_work(pooled_sessions) as uow, uow.begin() as tx:
            await tx.drafts.open_for(
                campaign_id=mirrored.id, stream_id=FLOW, rows=(), base_snapshot_hash=HASH
            )
            # True only if it got through AFTER the holder let go. A lock that stopped
            # blocking would make this False rather than hang the suite.
            return released.is_set()

    waited = await asyncio.wait_for(asyncio.gather(holder(), second_writer()), timeout=DEADLINE)
    assert waited[1] is True
