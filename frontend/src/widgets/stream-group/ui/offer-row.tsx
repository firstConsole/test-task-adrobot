import { OfferLabel } from '@/entities/offer'
import type { StreamRow } from '@/entities/stream'
import { RowActions } from '@/features/stream-draft'
import { Skeleton } from '@/shared/ui/skeleton'
import { TableCell, TableRow } from '@/shared/ui/table'

type OfferRowProps = {
  campaignId: string
  streamId: number
  /** Names the row for a screen reader: "Oxys in Flow 2", not "row 3 of 4". */
  streamName: string
  row: StreamRow
}

/**
 * One line of a flow's offer table.
 *
 * The share is printed, never computed. Every number on this screen was divided up by
 * `domain/shares.py`; a percentage worked out here would be a second implementation of that
 * arithmetic, and the day the two disagreed the screen would read 34/33/33 while Keitaro held
 * 33/33/33. `null` is an optimistic edit saying it does not know yet — the cell draws a
 * skeleton the width of the number rather than a stale one.
 */
export function OfferRow({ campaignId, streamId, streamName, row }: OfferRowProps) {
  const offerName = row.offer?.name ?? `#${String(row.offer_id)}`

  return (
    <TableRow aria-label={`${offerName} in ${streamName}`}>
      <TableCell className="whitespace-normal">
        <OfferLabel offerId={row.offer_id} offer={row.offer} withPreview />
      </TableCell>
      <TableCell className="tabular-nums">
        {row.share === null ? (
          <Skeleton className="h-4 w-9" aria-label="working out the new share" />
        ) : (
          `${String(row.share)}%`
        )}
      </TableCell>
      <TableCell />
      <TableCell />
      <TableCell className="text-right">
        <RowActions campaignId={campaignId} streamId={streamId} row={row} offerName={offerName} />
      </TableCell>
    </TableRow>
  )
}
