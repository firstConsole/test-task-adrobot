import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { campaignStreamsQuery } from '@/entities/stream'
import { api, unwrap } from '@/shared/api/client'
import { problemMessage } from '@/shared/lib/problem-message'

/**
 * Read this campaign and its flows from Keitaro again — `FETCH STREAMS FROM KT`.
 *
 * The answer is the campaign, not the flows, so this is the one place in the editor that
 * invalidates instead of splicing: the mirror underneath has been rewritten and the screen
 * has to be read again to see it. A flow the tracker no longer returns is kept and marked
 * absent rather than dropped, which is why pressing this can only ever add information.
 */
export function useSyncStreams(campaignId: string) {
  const queryClient = useQueryClient()
  const { queryKey } = campaignStreamsQuery(campaignId)

  const refetch = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/campaigns/{campaign_id}/refetch', {
          params: { path: { campaign_id: campaignId } },
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey })
      toast.success('Прочитано из Keitaro заново.')
    },
    onError: (error) => {
      toast.error(problemMessage(error, 'Keitaro не удалось прочитать.'))
    },
  })

  return {
    fetching: refetch.isPending,
    fetchStreams: () => {
      refetch.mutate()
    },
  }
}
