import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { api, unwrap } from '@/shared/api/client'
import { queryKeys } from '@/shared/api/query-keys'
import { problemMessage } from '@/shared/lib/problem-message'

import type { CreateCampaignValues } from '../model/schema'

/**
 * Build the campaign part 1 describes: one POST, and the tracker has a campaign with two
 * flows, a domain, a group and a source.
 *
 * No optimistic anything. The editor can afford to guess because it is rearranging rows it
 * already has; this writes a campaign into somebody's live tracker and gets back an id, a
 * public link and how far the setup actually got — none of which a client could invent.
 */
export function useCreateCampaign() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (values: CreateCampaignValues) =>
      unwrap(api.POST('/api/v1/campaigns', { body: values })),
    onSuccess: async (campaign) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.campaigns.all })
      toast.success(`${campaign.name} is in Keitaro.`)
    },
    onError: (error) => {
      toast.error(problemMessage(error, 'The campaign could not be created.'))
    },
  })
}
