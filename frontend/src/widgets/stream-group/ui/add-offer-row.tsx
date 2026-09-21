import { cn } from 'cn'
import { PlusIcon } from 'lucide-react'
import { useState } from 'react'

import { OfferCombobox, type Offer } from '@/entities/offer'
import { useDraftOps } from '@/features/stream-draft'
import { Button } from '@/shared/ui/button'
import { TableCell, TableRow } from '@/shared/ui/table'

import { BAND_STYLE, type GroupStatus } from '../model/appearance'
import { COLUMN_COUNT } from '../model/columns'

type AddOfferRowProps = {
  campaignId: string
  streamId: number
  streamName: string
  status: GroupStatus
}

/**
 * The footer of a flow's table: pick an offer, press `ADD`.
 *
 * Two controls and not one, as in the reference tool — picking is not committing, and a
 * combobox that staged an edit the moment the list was navigated with the arrow keys would
 * stage three of them on the way to the fourth entry.
 */
export function AddOfferRow({ campaignId, streamId, streamName, status }: AddOfferRowProps) {
  const [offer, setOffer] = useState<Offer | null>(null)
  const draft = useDraftOps(campaignId, streamId)

  return (
    <TableRow className={cn(BAND_STYLE[status])}>
      <TableCell colSpan={COLUMN_COUNT} className="whitespace-normal">
        <div className="flex items-center gap-2">
          <OfferCombobox
            value={offer}
            onChange={setOffer}
            disabled={draft.staging}
            className="max-w-xl"
          />
          <Button
            type="button"
            disabled={offer === null || draft.staging}
            aria-label={`Add an offer to ${streamName}`}
            onClick={() => {
              if (offer === null) return
              draft.add(offer)
              setOffer(null)
            }}
          >
            <PlusIcon />
            ADD
          </Button>
        </div>
      </TableCell>
    </TableRow>
  )
}
