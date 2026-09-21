import { ExternalLinkIcon, TriangleAlertIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link, useParams } from 'react-router'

import { canFinishSetup } from '@/entities/campaign'
import { useCampaignNumbers } from '@/entities/stats'
import { useCampaignStreams } from '@/entities/stream'
import { FinishSetupButton } from '@/features/repair-campaign'
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
  // One read for the whole screen. Every Stats cell below is a lookup in what this returns,
  // which is the difference between one report and one per row.
  const numbers = useCampaignNumbers(campaignId)

  if (streams.isPending) {
    return (
      <section className="space-y-4" aria-busy>
        <p className="sr-only" role="status">
          Читаю потоки…
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
        {problemMessage(streams.error, 'Потоки не удалось прочитать.')}
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
          Кампании
        </Link>
        <span aria-hidden>/</span>
        <span className="text-foreground font-medium">{campaign.name}</span>
        <span aria-hidden>/</span>
        <span>потоки в Keitaro</span>
      </nav>

      {campaign.setup_status === 'ready' ? null : (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
        >
          <TriangleAlertIcon className="mt-0.5 size-4 shrink-0" />
          <div className="space-y-2">
            <p>
              Потоки этой кампании в трекере так и не достроены.
              {campaign.setup_failure === null || campaign.setup_failure === undefined
                ? ''
                : ` ${campaign.setup_failure}`}{' '}
              {canFinishSetup(campaign)
                ? 'FINISH SETUP создаст то, чего не хватает в Keitaro, и не тронет остальное.'
                : 'Она построена не здесь, поэтому записи о том, какими её потоки задумывались, у нас нет: править их можно, достроить нельзя.'}
            </p>
            {canFinishSetup(campaign) ? <FinishSetupButton campaignId={campaignId} /> : null}
          </div>
        </div>
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
            зеркало обновлено {relativeTime(campaign.synced_at)}
          </span>
        )}
        {/* The day and the zone are the tracker's, not this browser's: "clicks today" means
            nothing until it says whose today. */}
        {numbers === null ? null : (
          <span className="text-muted-foreground text-xs">
            {numbers.available
              ? `кликов сегодня — ${numbers.day} ${numbers.timezone}${numbers.stale ? ', последнее удачное чтение' : ''}`
              : `кликов сегодня нет: ${numbers.unavailableReason ?? 'трекер не собрал отчёт'}`}
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
                В зеркале у этой кампании пока нет потоков. FETCH STREAMS FROM KT прочитает
                их из трекера заново.
              </td>
            </tr>
          </tbody>
        ) : (
          flows.map((flow) => (
            <StreamGroup
              key={flow.keitaro_stream_id}
              campaignId={campaignId}
              stream={flow}
              numbers={numbers}
            />
          ))
        )}
      </Frame>
    </section>
  )
}
