import { cn } from 'cn'

import { OfferLabel } from '@/entities/offer'
import { StatsCell, type CampaignNumbers } from '@/entities/stats'
import type { StreamRow } from '@/entities/stream'
import { RowActions, ShareCell } from '@/features/stream-draft'
import { TableCell, TableRow } from '@/shared/ui/table'

import { REMOVED_ROW, ROW_STYLE, type GroupStatus } from '../model/appearance'

type OfferRowProps = {
  campaignId: string
  streamId: number
  /** Names the row for a screen reader: "Oxys in Flow 2", not "row 3 of 4". */
  streamName: string
  row: StreamRow
  /** Dirty is a property of the flow, so the row is told rather than asked. */
  status: GroupStatus
  /** Read once for the whole screen; `null` until it arrives. */
  numbers: CampaignNumbers | null
}

/**
 * One line of a flow's offer table.
 *
 * A removed row is drawn, not hidden: greyed, at 0%, saying `(убран)` in words, and
 * offering `BRING BACK` where the others offer `REMOVE`. It survives a push — the flow is
 * written with that offer explicitly disabled rather than dropped — which is the behaviour
 * the reference tool is recognised by.
 */
export function OfferRow({
  campaignId,
  streamId,
  streamName,
  row,
  status,
  numbers,
}: OfferRowProps) {
  const offerName = row.offer?.name ?? `#${String(row.offer_id)}`

  return (
    <TableRow
      aria-label={`${offerName} в потоке ${streamName}${row.removed ? ', убран' : ''}`}
      className={cn(ROW_STYLE[status], row.removed ? REMOVED_ROW : null)}
    >
      <TableCell className="whitespace-normal">
        <OfferLabel offerId={row.offer_id} offer={row.offer} withPreview />
        {row.removed ? <span className="ml-1.5">(убран)</span> : null}
      </TableCell>
      <TableCell>
        <ShareCell
          campaignId={campaignId}
          streamId={streamId}
          row={row}
          offerName={offerName}
        />
      </TableCell>
      <TableCell>
        <StatsCell numbers={numbers} offerId={row.offer_id} offerName={offerName} />
      </TableCell>
      <TableCell className="text-right">
        <RowActions campaignId={campaignId} streamId={streamId} row={row} offerName={offerName} />
      </TableCell>
    </TableRow>
  )
}
