import { ExternalLinkIcon } from 'lucide-react'
import { Link, useParams } from 'react-router'

import { useCampaignStreams } from '@/entities/stream'
import { FetchStreamsButton } from '@/features/sync-streams'
import { ApiError } from '@/shared/api/client'
import { ROUTES } from '@/shared/config/routes'
import { problemMessage } from '@/shared/lib/problem-message'
import { Button } from '@/shared/ui/button'
import { Table } from '@/shared/ui/table'
import { COLUMN_COUNT, OfferTableHead, StreamGroup } from '@/widgets/stream-group'

export function CampaignStreamsPage() {
  const { campaignId } = useParams<'campaignId'>()

  // `/campaigns/:campaignId` cannot match without one. The throw is for the type, and for
  // the day somebody mounts this page under a route that can.
  if (campaignId === undefined) throw new Error('the campaign route matched without an id')

  return <CampaignStreams campaignId={campaignId} />
}

function CampaignStreams({ campaignId }: { campaignId: string }) {
  const streams = useCampaignStreams(campaignId)

  if (streams.isPending) {
    return <p className="text-muted-foreground text-sm">Reading the flows…</p>
  }

  if (streams.isError) {
    const correlationId =
      streams.error instanceof ApiError ? streams.error.correlationId : null
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
        <Link to={ROUTES.campaignList} className="hover:text-foreground underline-offset-4 hover:underline">
          Campaigns
        </Link>
        <span aria-hidden>/</span>
        <span className="text-foreground font-medium">{campaign.name}</span>
        <span aria-hidden>/</span>
        <span>Keitaro streams</span>
      </nav>

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
      </div>

      <div className="overflow-hidden rounded-lg border">
        <Table>
          <OfferTableHead />
          {flows.length === 0 ? (
            <tbody>
              <tr>
                <td colSpan={COLUMN_COUNT} className="text-muted-foreground p-6 text-center text-sm">
                  This campaign has no flows in the mirror yet.
                </td>
              </tr>
            </tbody>
          ) : (
            flows.map((flow) => <StreamGroup key={flow.keitaro_stream_id} campaignId={campaignId} stream={flow} />)
          )}
        </Table>
      </div>
    </section>
  )
}
