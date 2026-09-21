import { useIsMutating, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import type { Offer } from '@/entities/offer'
import { campaignStreamsQuery } from '@/entities/stream'
import { api, unwrap } from '@/shared/api/client'
import { problemMessage } from '@/shared/lib/problem-message'

import type { DraftOperation } from '../lib/optimistic'
import { withOperation, withStream } from '../lib/optimistic'

/** Scopes the in-flight check to one flow, so a busy Flow 2 does not freeze Flow 1. */
function draftMutationKey(campaignId: string, streamId: number) {
  return ['stream-draft', campaignId, streamId] as const
}

/**
 * Stage one edit on one flow's draft.
 *
 * Every endpoint of the editor answers with the whole flow, so success is not a refetch but a
 * splice: the server's rows, shares and verdicts replace what optimism guessed. A failure
 * puts back the snapshot taken before the press and says why in a toast — an edit that was
 * refused must not be left looking as though it landed.
 */
export function useDraftOps(campaignId: string, streamId: number) {
  const queryClient = useQueryClient()
  const { queryKey } = campaignStreamsQuery(campaignId)
  const mutationKey = draftMutationKey(campaignId, streamId)

  const edit = useMutation({
    mutationKey,
    mutationFn: (operation: DraftOperation) =>
      unwrap(
        api.POST('/api/v1/campaigns/{campaign_id}/streams/{stream_id}/draft/operations', {
          params: { path: { campaign_id: campaignId, stream_id: streamId } },
          body: { operations: [{ kind: operation.kind, offer_id: operation.offerId }] },
        }),
      ),

    onMutate: async (operation) => {
      // A GET already on its way would land after the optimistic write and undo it.
      await queryClient.cancelQueries({ queryKey })
      const previous = queryClient.getQueryData(queryKey)

      if (previous !== undefined) {
        const flow = previous.streams.find((one) => one.keitaro_stream_id === streamId)
        if (flow !== undefined) {
          queryClient.setQueryData(queryKey, withStream(previous, withOperation(flow, operation)))
        }
      }

      return { previous }
    },

    onSuccess: (stream) => {
      queryClient.setQueryData(queryKey, (view) =>
        view === undefined ? view : withStream(view, stream),
      )
    },

    onError: (error, _operation, context) => {
      if (context?.previous !== undefined) queryClient.setQueryData(queryKey, context.previous)
      toast.error(problemMessage(error, 'The edit could not be staged.'))
    },
  })

  const staging = useIsMutating({ mutationKey }) > 0

  return {
    /** True while any edit of this flow is in flight, whichever button started it. */
    staging,
    add: (offer: Offer) => {
      edit.mutate({ kind: 'add', offerId: offer.id, offer })
    },
    remove: (offerId: number) => {
      edit.mutate({ kind: 'remove', offerId })
    },
    bringBack: (offerId: number) => {
      edit.mutate({ kind: 'bring_back', offerId })
    },
  }
}
