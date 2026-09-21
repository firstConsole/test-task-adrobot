import { TriangleAlertIcon, UploadIcon, XIcon } from 'lucide-react'

import type { Stream } from '@/entities/stream'
import { Button } from '@/shared/ui/button'

import { useDraftPush } from '../api/use-draft-ops'
import { ConflictDialog } from './conflict-dialog'

type DraftActionsProps = {
  campaignId: string
  stream: Stream
}

/**
 * `PUSH TO KT` and `CANCEL`, rendered only while the flow is dirty.
 *
 * Whether the push may go is `can_push`, decided on the server, and the sentence under a
 * disabled button is the server's too — "Flow 2 would have no active offer left, its traffic
 * would go nowhere" and not `constraint violation`. A frontend that worked the rule out for
 * itself would be a second implementation of the rule.
 */
export function DraftActions({ campaignId, stream }: DraftActionsProps) {
  const draft = useDraftPush(campaignId, stream.keitaro_stream_id)
  const blocked = stream.block_reason

  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <Button
        type="button"
        size="sm"
        disabled={!stream.can_push || draft.working}
        aria-label={`Push ${stream.name} to Keitaro`}
        onClick={draft.push}
      >
        <UploadIcon />
        PUSH TO KT
      </Button>

      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={draft.working}
        aria-label={`Throw away the staged edits of ${stream.name}`}
        onClick={draft.discard}
      >
        <XIcon />
        CANCEL
      </Button>

      {blocked === null || blocked === undefined ? null : (
        <span className="flex items-center gap-1 text-xs font-normal text-amber-800">
          <TriangleAlertIcon className="size-3.5" />
          {blocked}
        </span>
      )}

      {stream.warnings.map((warning) => (
        <span key={warning} className="text-muted-foreground text-xs font-normal">
          {warning}
        </span>
      ))}

      <ConflictDialog
        conflict={draft.conflict}
        stream={stream}
        working={draft.working}
        onOverwrite={draft.pushOver}
        onDismiss={draft.dismissConflict}
      />
    </div>
  )
}
