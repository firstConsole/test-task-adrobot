"""The campaign list: one page, newest first, and where the next page starts.

Thin enough to be tempting to inline into the router, and it is not inlined for the reason
every other scenario is not: the HTTP layer would then be the thing that decides how a
campaign is rendered, and there are already four other places that answer with one.

Keyset and never an offset — the repository's own docstring explains what an offset does to
a list while a campaign is being created. What is decided *here* is only that the page is
read in one transaction, which is what makes the row that ends it and the cursor that points
past it agree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adrobot.application.dto import CampaignsPage, CampaignView

if TYPE_CHECKING:
    from adrobot.application.dto import ListCampaignsQuery
    from adrobot.application.ports.persistence import UnitOfWork


class ListCampaigns:
    """One page of campaigns, with the public link built for each."""

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    async def __call__(self, query: ListCampaignsQuery) -> CampaignsPage:
        """Read one page. `limit` arrives already bounded — the API refuses an unusable one."""
        async with self._uow.begin() as transaction:
            page = await transaction.campaigns.page(
                after=query.after, query=query.query, limit=query.limit
            )
        return CampaignsPage(
            campaigns=tuple(CampaignView.of(row) for row in page.campaigns),
            next_cursor=page.next_cursor,
        )
