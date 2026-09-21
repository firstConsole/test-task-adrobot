import { RotateCcwIcon, Trash2Icon } from 'lucide-react'

import type { StreamRow } from '@/entities/stream'
import { Button } from '@/shared/ui/button'

import { useDraftOps } from '../api/use-draft-ops'

type RowActionsProps = {
  campaignId: string
  streamId: number
  row: StreamRow
  /** For the button's accessible name — "REMOVE" alone is four identical buttons. */
  offerName: string
}

/**
 * The one button a row offers, which is `REMOVE` or `BRING BACK` and never both.
 *
 * A removed row keeps its place and its label and swaps its action — the behaviour the
 * reference tool is recognisable by, and the reason a removal survives a push instead of
 * quietly dropping the offer out of the flow.
 */
export function RowActions({ campaignId, streamId, row, offerName }: RowActionsProps) {
  const draft = useDraftOps(campaignId, streamId)

  return row.removed ? (
    <Button
      type="button"
      variant="outline"
      size="sm"
      disabled={draft.staging}
      aria-label={`Bring ${offerName} back`}
      onClick={() => {
        draft.bringBack(row.offer_id)
      }}
    >
      <RotateCcwIcon />
      BRING BACK
    </Button>
  ) : (
    <Button
      type="button"
      variant="outline"
      size="sm"
      disabled={draft.staging}
      aria-label={`Remove ${offerName}`}
      onClick={() => {
        draft.remove(row.offer_id)
      }}
    >
      <Trash2Icon />
      REMOVE
    </Button>
  )
}
