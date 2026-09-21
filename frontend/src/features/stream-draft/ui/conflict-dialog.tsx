import { cn } from 'cn'

import type { Stream } from '@/entities/stream'
import type { components } from '@/shared/api/schema.gen'
import { Button } from '@/shared/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog'

type ConflictingState = components['schemas']['ConflictingState']
type OfferShare = components['schemas']['OfferShare']

/** One offer's two readings, `undefined` on a side meaning that side has no such row. */
type Side = { offerId: number; name: string | null; theirs?: OfferShare; ours?: OfferShare }

function describe(share: OfferShare | undefined): string {
  if (share === undefined) return '—'
  return share.state === 'active' ? `${String(share.share)}%` : `${String(share.share)}% disabled`
}

/**
 * Line the two readings up by offer, which is the only way a person can compare them.
 *
 * Nothing is computed here and nothing is decided: both lists arrived on the 409, and this
 * walks their union so that a row present on one side and missing on the other is visible
 * as such rather than as a shorter list.
 */
function sides(conflict: ConflictingState, stream: Stream): Side[] {
  const theirs = new Map(conflict.tracker_holds.map((row) => [row.offer_id, row]))
  const ours = new Map(conflict.push_would_write.map((row) => [row.offer_id, row]))
  const offerIds = [...new Set([...theirs.keys(), ...ours.keys()])]

  return offerIds.map((offerId) => ({
    offerId,
    name: stream.rows.find((row) => row.offer_id === offerId)?.offer?.name ?? null,
    theirs: theirs.get(offerId),
    ours: ours.get(offerId),
  }))
}

type ConflictDialogProps = {
  conflict: ConflictingState | null
  stream: Stream
  working: boolean
  onOverwrite: () => void
  onDismiss: () => void
}

/**
 * What to do about a flow somebody edited in Keitaro while this draft was open.
 *
 * A body that only said "conflict" would leave the person with nothing to decide between, so
 * the 409 carries both readings and this puts them side by side. There is no automatic merge
 * and no retry: the two states are shown, and a human picks.
 *
 * The third way out — start again from what the tracker holds — is `FETCH STREAMS FROM KT`,
 * which is already on the toolbar behind this dialog. It is named here rather than repeated
 * as a button, because re-reading the mirror is not a decision about this push.
 */
export function ConflictDialog({
  conflict,
  stream,
  working,
  onOverwrite,
  onDismiss,
}: ConflictDialogProps) {
  if (conflict === null) return null

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onDismiss()
      }}
    >
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{stream.name} has moved in Keitaro</DialogTitle>
          <DialogDescription>
            Somebody edited this flow in the tracker after this draft was opened, so nothing has
            been written. Close this and press FETCH STREAMS FROM KT to start again from what
            the tracker holds — or write over it.
          </DialogDescription>
        </DialogHeader>

        <table className="w-full text-sm">
          <thead>
            <tr className="text-muted-foreground border-border border-b text-left text-xs">
              <th scope="col" className="py-1 font-medium">
                Offer
              </th>
              <th scope="col" className="py-1 font-medium">
                In Keitaro now
              </th>
              <th scope="col" className="py-1 font-medium">
                Push would write
              </th>
            </tr>
          </thead>
          <tbody>
            {sides(conflict, stream).map((side) => {
              const differs = describe(side.theirs) !== describe(side.ours)
              return (
                <tr key={side.offerId} className={cn('border-border border-b last:border-0')}>
                  <td className="max-w-64 py-1.5">
                    <span className="text-muted-foreground tabular-nums">
                      #{String(side.offerId)}
                    </span>
                    {side.name === null ? null : (
                      <span className="ml-1.5 inline-block max-w-48 truncate align-bottom">
                        {side.name}
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 tabular-nums">{describe(side.theirs)}</td>
                  <td className={cn('py-1.5 tabular-nums', differs && 'font-medium text-amber-800')}>
                    {differs ? <span className="sr-only">differs: </span> : null}
                    {describe(side.ours)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onDismiss}>
            LEAVE IT
          </Button>
          <Button type="button" variant="destructive" disabled={working} onClick={onOverwrite}>
            OVERWRITE KEITARO
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
