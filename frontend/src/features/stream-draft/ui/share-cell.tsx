import { PinIcon } from 'lucide-react'

import type { StreamRow } from '@/entities/stream'
import { Button } from '@/shared/ui/button'
import { Skeleton } from '@/shared/ui/skeleton'

import { usePinOffer } from '../api/use-draft-ops'

type ShareCellProps = {
  campaignId: string
  streamId: number
  row: StreamRow
  /** So four identical pins are four different buttons to a screen reader. */
  offerName: string
}

/**
 * One row's share, and the pin that holds it there.
 *
 * The number is printed, never computed: every percentage on this screen was divided up by
 * `domain/shares.py`. `null` is an edit in flight saying it does not know yet — the cell draws
 * a skeleton the width of the number rather than a stale one, and the pin is not offered,
 * because "hold it where it is" has no answer while "where it is" is being decided.
 */
export function ShareCell({ campaignId, streamId, row, offerName }: ShareCellProps) {
  const pin = usePinOffer(campaignId, streamId)
  const pinned = row.pinned_share !== null && row.pinned_share !== undefined

  if (row.share === null) {
    return <Skeleton className="h-4 w-9" aria-label={`считаю долю оффера ${offerName}`} />
  }

  return (
    <span className="inline-flex items-center gap-0.5">
      <span className="tabular-nums">{String(row.share)}%</span>
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        aria-pressed={pinned}
        disabled={pin.pinning}
        title={pinned ? 'Отпустить строку' : 'Держать строку на этой доле'}
        aria-label={
          pinned ? `Открепить ${offerName}` : `Закрепить ${offerName} на ${String(row.share)} процентах`
        }
        onClick={() => {
          pin.setPin(row.offer_id, !pinned)
        }}
      >
        <PinIcon className={pinned ? 'fill-current' : 'text-muted-foreground/60'} />
      </Button>
    </span>
  )
}
