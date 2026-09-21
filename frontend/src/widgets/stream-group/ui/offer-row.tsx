import { cn } from 'cn'

import { OfferLabel } from '@/entities/offer'
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
}

/**
 * One line of a flow's offer table.
 *
 * A removed row is drawn, not hidden: greyed, at 0%, saying `(removed)` in words, and
 * offering `BRING BACK` where the others offer `REMOVE`. It survives a push — the flow is
 * written with that offer explicitly disabled rather than dropped — which is the behaviour
 * the reference tool is recognised by.
 */
export function OfferRow({ campaignId, streamId, streamName, row, status }: OfferRowProps) {
  const offerName = row.offer?.name ?? `#${String(row.offer_id)}`

  return (
    <TableRow
      aria-label={`${offerName} in ${streamName}${row.removed ? ', removed' : ''}`}
      className={cn(ROW_STYLE[status], row.removed ? REMOVED_ROW : null)}
    >
      <TableCell className="whitespace-normal">
        <OfferLabel offerId={row.offer_id} offer={row.offer} withPreview />
        {row.removed ? <span className="ml-1.5">(removed)</span> : null}
      </TableCell>
      <TableCell>
        <ShareCell
          campaignId={campaignId}
          streamId={streamId}
          row={row}
          offerName={offerName}
        />
      </TableCell>
      <TableCell />
      <TableCell />
      <TableCell className="text-right">
        <RowActions campaignId={campaignId} streamId={streamId} row={row} offerName={offerName} />
      </TableCell>
    </TableRow>
  )
}
