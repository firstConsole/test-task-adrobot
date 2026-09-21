import { DownloadIcon } from 'lucide-react'

import { Button } from '@/shared/ui/button'

import { useSyncOffers } from '../api/use-sync-offers'

/**
 * `FETCH OFFERS FROM KT`, sized to sit inside the empty dropdown it fixes.
 *
 * Named after `FETCH STREAMS FROM KT` on purpose: both buttons do the same thing to two
 * halves of the same mirror, and a person who has pressed one knows what the other does.
 */
export function SyncOffersButton() {
  const sync = useSyncOffers()

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      disabled={sync.syncing}
      onClick={sync.syncOffers}
    >
      <DownloadIcon />
      {sync.syncing ? 'FETCHING…' : 'FETCH OFFERS FROM KT'}
    </Button>
  )
}
