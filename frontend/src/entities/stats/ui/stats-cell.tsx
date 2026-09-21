import { Skeleton } from '@/shared/ui/skeleton'

import type { CampaignNumbers } from '../lib/numbers'

type StatsCellProps = {
  numbers: CampaignNumbers | null
  offerId: number
  /** For the cell's accessible name — "12 clicks" on its own names no offer. */
  offerName: string
}

/**
 * One offer's clicks today.
 *
 * **This component issues no request.** Everything it needs was read once for the whole
 * screen and handed down as a map; forty rows are forty lookups, not forty reports. There is
 * a test on exactly that, because the N+1 this table invites is the kind that only shows up
 * under a campaign with more offers than a demo has.
 *
 * An offer the report did not mention gets an empty cell rather than a zero: the tracker said
 * nothing about it, and a zero written here would be this screen inventing a measurement.
 */
export function StatsCell({ numbers, offerId, offerName }: StatsCellProps) {
  if (numbers === null) {
    return <Skeleton className="h-4 w-8" aria-label={`reading the clicks of ${offerName}`} />
  }

  if (!numbers.available) {
    return (
      <span
        className="text-muted-foreground"
        title={numbers.unavailableReason ?? 'The tracker would not build the report.'}
      >
        <span aria-hidden>—</span>
        <span className="sr-only">no numbers for {offerName}</span>
      </span>
    )
  }

  const row = numbers.byOffer.get(offerId)
  if (row === undefined) return null

  return (
    <span className="tabular-nums">
      {String(row.clicks)}
      <span className="sr-only"> clicks</span>
      {row.conversions === 0 ? null : (
        <span className="text-muted-foreground"> · {String(row.conversions)} cv</span>
      )}
    </span>
  )
}
