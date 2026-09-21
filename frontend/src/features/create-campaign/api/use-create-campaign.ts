import { useMutation, useQueryClient } from '@tanstack/react-query'

import { api, unwrap } from '@/shared/api/client'
import { queryKeys } from '@/shared/api/query-keys'

import type { CreateCampaignValues } from '../model/schema'

/**
 * Build the campaign part 1 describes: one POST, and the tracker has a campaign with two
 * flows, a domain, a group and a source.
 *
 * No optimistic anything. The editor can afford to guess because it is rearranging rows it
 * already has; this writes a campaign into somebody's live tracker and gets back an id, a
 * public link and how far the setup actually got — none of which a client could invent.
 *
 * What happens *next* is not here. This hook owns the request and the cache; where the
 * person is sent and what they are told belongs to the screen, and a mutation that also
 * navigated could not be called from anywhere else.
 */
export function useCreateCampaign() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (values: CreateCampaignValues) =>
      unwrap(api.POST('/api/v1/campaigns', { body: values })),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.campaigns.all })
    },
    // No `onError` here, and that is the point: a refused creation belongs under the field
    // it was refused over, and only the form knows which input that is. A toast on top of
    // three red fields would be the same news told twice.
  })
}
