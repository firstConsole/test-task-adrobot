import { useIsMutating, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'

import type { Offer } from '@/entities/offer'
import { campaignStreamsQuery, type StreamAnswer } from '@/entities/stream'
import { ApiError, api, unwrap } from '@/shared/api/client'
import type { components } from '@/shared/api/schema.gen'
import { queryKeys } from '@/shared/api/query-keys'
import { problemMessage } from '@/shared/lib/problem-message'

import type { DraftOperation } from '../lib/optimistic'
import { withOperation, withPin, withStream } from '../lib/optimistic'

type ConflictingState = components['schemas']['ConflictingState']

/** Scopes the in-flight check to one flow, so a busy Flow 2 does not freeze Flow 1. */
function draftMutationKey(campaignId: string, streamId: number) {
  return ['stream-draft', campaignId, streamId] as const
}

/** `50/50` — what the tracker now holds, read off the answer rather than worked out. */
function activeShares(stream: StreamAnswer): string {
  return stream.rows
    .filter((row) => !row.removed)
    .map((row) => String(row.share))
    .join('/')
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
      toast.error(problemMessage(error, 'Правку не удалось отложить в черновик.'))
    },
  })

  const staging = useIsMutating({ mutationKey }) > 0

  return {
    /** True while anything at all is in flight on this flow, whichever button started it. */
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

/**
 * Write one flow's draft to Keitaro, or throw the draft away.
 *
 * Neither is optimistic, and deliberately so: until the tracker has answered, nobody knows
 * what it holds, and a screen that showed the new state early would be reporting a write that
 * may not have happened. The push confirms itself with the numbers it wrote — `50/50` — so
 * that the sentence on screen can be checked against the other window without leaving it.
 *
 * `CANCEL` does not touch the pins. They live in the mirror, not in the draft, which is what
 * makes them survive both of these buttons — the reference tool behaves the same way.
 *
 * A 409 is not an error to report and move on from: the body carries both readings of the
 * flow, so it is held here until somebody decides between them. Nothing is retried on its
 * own — writing over another person's work is a decision, never a retry.
 */
export function useDraftPush(campaignId: string, streamId: number) {
  const queryClient = useQueryClient()
  const { queryKey } = campaignStreamsQuery(campaignId)
  const mutationKey = draftMutationKey(campaignId, streamId)
  const [conflict, setConflict] = useState<ConflictingState | null>(null)

  const land = (stream: StreamAnswer) => {
    queryClient.setQueryData(queryKey, (view) =>
      view === undefined ? view : withStream(view, stream),
    )
  }

  const push = useMutation({
    mutationKey,
    mutationFn: (overwrite: boolean) =>
      unwrap(
        api.POST('/api/v1/campaigns/{campaign_id}/streams/{stream_id}/draft/push', {
          params: { path: { campaign_id: campaignId, stream_id: streamId } },
          body: { overwrite },
        }),
      ),
    onSuccess: (stream) => {
      setConflict(null)
      land(stream)
      void queryClient.invalidateQueries({ queryKey: queryKeys.campaigns.stats(campaignId) })
      toast.success(`Записано в Keitaro: ${activeShares(stream)}`)
    },
    onError: (error) => {
      const clash = error instanceof ApiError ? (error.problem.conflict ?? null) : null
      if (clash !== null) {
        setConflict(clash)
        return
      }
      toast.error(problemMessage(error, 'Keitaro не принял изменение.'))
    },
  })

  const discard = useMutation({
    mutationKey,
    mutationFn: () =>
      unwrap(
        api.DELETE('/api/v1/campaigns/{campaign_id}/streams/{stream_id}/draft', {
          params: { path: { campaign_id: campaignId, stream_id: streamId } },
        }),
      ),
    onSuccess: land,
    onError: (error) => {
      toast.error(problemMessage(error, 'Черновик не удалось выбросить.'))
    },
  })

  return {
    working: useIsMutating({ mutationKey }) > 0,
    /** The two readings of the flow a 409 came back with, until somebody chooses. */
    conflict,
    dismissConflict: () => {
      setConflict(null)
    },
    push: () => {
      push.mutate(false)
    },
    /** The answer to a 409 and nothing else — it writes over what the tracker holds. */
    pushOver: () => {
      push.mutate(true)
    },
    discard: () => {
      discard.mutate()
    },
  }
}

/**
 * Hold one row where it is, or let it go again.
 *
 * **A pin does not make the draft dirty and starts no redivision.** It looks like a bug and
 * is not: the reference tool behaves the same way, and holding a row is a statement about the
 * *next* division, not a division of its own. The pin also lives in the mirror rather than in
 * the draft, which is what lets it survive both `PUSH TO KT` and `CANCEL`.
 *
 * This is the one edit the client can apply in full, because it involves no arithmetic: the
 * row keeps the share it already shows, and no other row moves. So the optimistic write here
 * blanks nothing and sets no `dirty` flag.
 */
export function usePinOffer(campaignId: string, streamId: number) {
  const queryClient = useQueryClient()
  const { queryKey } = campaignStreamsQuery(campaignId)
  const mutationKey = draftMutationKey(campaignId, streamId)

  const toggle = useMutation({
    mutationKey,
    mutationFn: ({ offerId, pin }: { offerId: number; pin: boolean }) => {
      const params = {
        path: { campaign_id: campaignId, stream_id: streamId, offer_id: offerId },
      }
      const path = '/api/v1/campaigns/{campaign_id}/streams/{stream_id}/offers/{offer_id}/pin'
      // No share on the wire: "hold it where it is" is what the button offers, and the
      // server reads the row's current share for itself.
      return pin
        ? unwrap(api.PUT(path, { params, body: {} }))
        : unwrap(api.DELETE(path, { params }))
    },

    onMutate: async ({ offerId, pin }) => {
      await queryClient.cancelQueries({ queryKey })
      const previous = queryClient.getQueryData(queryKey)

      if (previous !== undefined) {
        const flow = previous.streams.find((one) => one.keitaro_stream_id === streamId)
        if (flow !== undefined) {
          queryClient.setQueryData(queryKey, withStream(previous, withPin(flow, offerId, pin)))
        }
      }

      return { previous }
    },

    onSuccess: (stream) => {
      queryClient.setQueryData(queryKey, (view) =>
        view === undefined ? view : withStream(view, stream),
      )
    },

    onError: (error, _variables, context) => {
      if (context?.previous !== undefined) queryClient.setQueryData(queryKey, context.previous)
      toast.error(problemMessage(error, 'Закрепить строку не удалось.'))
    },
  })

  return {
    pinning: useIsMutating({ mutationKey }) > 0,
    setPin: (offerId: number, pin: boolean) => {
      toggle.mutate({ offerId, pin })
    },
  }
}
