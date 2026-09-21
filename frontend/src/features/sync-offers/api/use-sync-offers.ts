import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { api, unwrap } from '@/shared/api/client'
import { queryKeys } from '@/shared/api/query-keys'
import { problemMessage } from '@/shared/lib/problem-message'

/**
 * Read the tracker's whole offer list again — the catalogue every combobox searches.
 *
 * There is a button for this at all because `GET /offers` in Keitaro takes no query
 * parameters: the catalogue is mirrored here or it is not searchable anywhere. A deployment
 * that has never synced therefore has an empty combobox on its very first screen, which is
 * indistinguishable from "no such offer" unless something says otherwise — so this is
 * offered exactly where that emptiness shows.
 *
 * Every offer key is invalidated and not one search: the copy underneath was rewritten, so
 * every term asked before this is now a stale answer.
 */
export function useSyncOffers() {
  const queryClient = useQueryClient()

  const sync = useMutation({
    mutationFn: () => unwrap(api.POST('/api/v1/offers/sync', {})),
    onSuccess: async (catalogue) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.offers.all })

      // `synced_at` is null when the tracker listed nothing, and the copy was left alone —
      // reporting "0 offers" there would read as "the catalogue was emptied".
      if (catalogue.synced_at === null) {
        toast.warning('Keitaro не отдал ни одного оффера — каталог оставлен как был.')
        return
      }
      toast.success(`Из Keitaro прочитано офферов: ${String(catalogue.offers)}.`)
    },
    onError: (error) => {
      toast.error(problemMessage(error, 'Каталог офферов не удалось прочитать.'))
    },
  })

  return {
    syncing: sync.isPending,
    syncOffers: () => {
      sync.mutate()
    },
  }
}
