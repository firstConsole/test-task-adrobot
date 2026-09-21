import { ExternalLinkIcon } from 'lucide-react'
import { Link } from 'react-router'

import { campaignStreamsPath } from '@/shared/config/routes'
import { relativeTime } from '@/shared/lib/relative-time'
import { Badge } from '@/shared/ui/badge'
import { TableCell, TableRow } from '@/shared/ui/table'

import type { Campaign } from '../model/types'

/** Kept beside the row so the page's empty state can span exactly this many cells. */
export const CAMPAIGN_COLUMN_COUNT = 6

/**
 * One campaign, and the two places it can be opened from.
 *
 * The name leads into our editor and the last cell leads into Keitaro, because those are the
 * two questions anyone has about a row here: what is in this campaign, and does the tracker
 * agree. The tracker's own address is never in this bundle — the link arrives on the
 * campaign, built by the layer that holds the configuration.
 *
 * Status says nothing when there is nothing to say. A badge on every row for `ready` trains
 * the eye to skip the column, which is exactly the column that has to be noticed on the one
 * row where a campaign's flows were never finished.
 */
export function CampaignRow({ campaign }: { campaign: Campaign }) {
  return (
    <TableRow>
      <TableCell>
        <Link
          to={campaignStreamsPath(campaign.id)}
          className="font-medium underline-offset-4 hover:underline"
        >
          {campaign.name}
        </Link>
        <span className="text-muted-foreground block text-xs">/{campaign.alias}</span>
      </TableCell>

      <TableCell className="text-muted-foreground tabular-nums">
        {campaign.keitaro_campaign_id}
      </TableCell>

      {/* Null for a campaign somebody else built: the geo we were asked for is a thing this
          service remembers about a creation, not a thing the tracker keeps. */}
      <TableCell className="tabular-nums">{campaign.requested_country ?? '—'}</TableCell>

      <TableCell className="space-x-1.5">
        {campaign.setup_status === 'ready' ? null : (
          <Badge className="border-amber-300 bg-amber-50 text-amber-900">не достроена</Badge>
        )}
        {campaign.state === 'active' ? null : (
          <Badge variant="destructive">{campaign.state}</Badge>
        )}
      </TableCell>

      <TableCell className="text-muted-foreground whitespace-nowrap">
        {relativeTime(campaign.created_at)}
      </TableCell>

      <TableCell className="text-right">
        <a
          href={campaign.tracker_url}
          target="_blank"
          rel="noreferrer"
          className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs underline-offset-4 hover:underline"
        >
          VIEW IN KT
          <ExternalLinkIcon className="size-3" />
        </a>
      </TableCell>
    </TableRow>
  )
}
