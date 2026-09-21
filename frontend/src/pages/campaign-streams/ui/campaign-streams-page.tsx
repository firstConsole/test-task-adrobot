import { ExternalLinkIcon, TriangleAlertIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link, useParams } from 'react-router'

import { useCampaignStreams } from '@/entities/stream'
import { FetchStreamsButton } from '@/features/sync-streams'
import { ApiError } from '@/shared/api/client'
import { ROUTES } from '@/shared/config/routes'
import { problemMessage } from '@/shared/lib/problem-message'
import { relativeTime } from '@/shared/lib/relative-time'
import { Button } from '@/shared/ui/button'
import { Table } from '@/shared/ui/table'
import {
  COLUMN_COUNT,
  OfferTableHead,
  StreamGroup,
  StreamGroupSkeleton,
} from '@/widgets/stream-group'

const SKELETON_GROUPS = [0, 1]

export function CampaignStreamsPage() {
  const { campaignId } = useParams<'campaignId'>()

  // `/campaigns/:campaignId` cannot match without one. The throw is for the type, and for
  // the day somebody mounts this page under a route that can.
  if (campaignId === undefined) throw new Error('the campaign route matched without an id')

  return <CampaignStreams campaignId={campaignId} />
}

function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-lg border">
      <Table>
        <OfferTableHead />
        {children}
      </Table>
    </div>
  )
}

function CampaignStreams({ campaignId }: { campaignId: string }) {
  const streams = useCampaignStreams(campaignId)

  if (streams.isPending) {
    return (
      <section className="space-y-4" aria-busy>
        <p className="sr-only" role="status">
          Reading the flows…
        </p>
        <Frame>
          {SKELETON_GROUPS.map((group) => (
            <StreamGroupSkeleton key={group} />
          ))}
        </Frame>
      </section>
    )
  }

  if (streams.isError) {
    const correlationId = streams.error instanceof ApiError ? streams.error.correlationId : null
    return (
      <p role="alert" className="text-destructive text-sm">
        {problemMessage(streams.error, 'The flows could not be read.')}
        {correlationId === null ? null : ` (${correlationId})`}
      </p>
    )
  }

  const { campaign, streams: flows } = streams.data

  return (
    <section className="space-y-4">
      <nav className="text-muted-foreground flex items-center gap-1.5 text-sm">
        <Link
          to={ROUTES.campaignList}
          className="hover:text-foreground underline-offset-4 hover:underline"
        >
          Campaigns
        </Link>
        <span aria-hidden>/</span>
        <span className="text-foreground font-medium">{campaign.name}</span>
        <span aria-hidden>/</span>
        <span>Keitaro streams</span>
      </nav>

      {campaign.setup_status === 'ready' ? null : (
        <p
          role="alert"
          className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
        >
          <TriangleAlertIcon className="mt-0.5 size-4 shrink-0" />
          <span>
            This campaign's flows were never finished in the tracker.
            {campaign.setup_failure === null || campaign.setup_failure === undefined
              ? ''
              : ` ${campaign.setup_failure}`}
          </span>
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <FetchStreamsButton campaignId={campaignId} />
        {/* The address of the tracker is never in this bundle: the link arrives on the
            campaign, built by the layer that holds the configuration. */}
        <Button asChild variant="outline">
          <a href={campaign.tracker_url} target="_blank" rel="noreferrer">
            VIEW IN KT
            <ExternalLinkIcon />
          </a>
        </Button>
        {/* When the mirror was last read, stated rather than judged: "stale" would need a
            threshold this service has no basis to pick, and the button to fix it is right
            here anyway. */}
        {campaign.synced_at === null || campaign.synced_at === undefined ? null : (
          <span className="text-muted-foreground text-xs">
            mirrored {relativeTime(campaign.synced_at)}
          </span>
        )}
      </div>

      <Frame>
        {flows.length === 0 ? (
          <tbody>
            <tr>
              <td
                colSpan={COLUMN_COUNT}
                className="text-muted-foreground p-6 text-center text-sm"
              >
                This campaign has no flows in the mirror yet. FETCH STREAMS FROM KT will read
                them again from the tracker.
              </td>
            </tr>
          </tbody>
        ) : (
          flows.map((flow) => (
            <StreamGroup key={flow.keitaro_stream_id} campaignId={campaignId} stream={flow} />
          ))
        )}
      </Frame>
    </section>
  )
}
