import { OfferLabel } from '@/entities/offer'
import type { StreamRow } from '@/entities/stream'
import { TableCell, TableRow } from '@/shared/ui/table'

type OfferRowProps = {
  row: StreamRow
  /** Names the row for a screen reader: "Oxys in Flow 2", not "row 3 of 4". */
  streamName: string
}

/**
 * One line of a flow's offer table.
 *
 * The share is printed, never computed. Every number on this screen was divided up by
 * `domain/shares.py`; a percentage worked out here would be a second implementation of that
 * arithmetic, and the day the two disagreed the screen would read 34/33/33 while Keitaro
 * held 33/33/33.
 */
export function OfferRow({ row, streamName }: OfferRowProps) {
  const name = row.offer?.name ?? `#${String(row.offer_id)}`

  return (
    <TableRow aria-label={`${name} in ${streamName}`}>
      <TableCell className="whitespace-normal">
        <OfferLabel offerId={row.offer_id} offer={row.offer} withPreview />
      </TableCell>
      <TableCell className="tabular-nums">{String(row.share)}%</TableCell>
      <TableCell />
      <TableCell />
      <TableCell className="text-right" />
    </TableRow>
  )
}
