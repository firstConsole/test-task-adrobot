import { DownloadIcon } from 'lucide-react'

import { Button } from '@/shared/ui/button'

import { useSyncStreams } from '../api/use-sync-streams'

/**
 * `FETCH STREAMS FROM KT`: pull the tracker's own state over the mirror.
 *
 * This is how the editor opens a campaign somebody edited in Keitaro directly, and how a 409
 * from a push is resolved by looking rather than by guessing.
 */
export function FetchStreamsButton({ campaignId }: { campaignId: string }) {
  const sync = useSyncStreams(campaignId)

  return (
    <Button type="button" variant="outline" disabled={sync.fetching} onClick={sync.fetchStreams}>
      <DownloadIcon />
      {sync.fetching ? 'FETCHING…' : 'FETCH STREAMS FROM KT'}
    </Button>
  )
}
