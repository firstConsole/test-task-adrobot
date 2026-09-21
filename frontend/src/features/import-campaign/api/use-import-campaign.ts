import { useMutation, useQueryClient } from '@tanstack/react-query'

import { api, unwrap } from '@/shared/api/client'
import { queryKeys } from '@/shared/api/query-keys'

/**
 * Mirror a campaign the tracker already has, which is what makes this an editor of
 * *existing* campaigns rather than only of the ones we made.
 *
 * The reference video edits campaign 93212, built by hand months before this service
 * existed. Without this call there would be no way to open it.
 */
export function useImportCampaign() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (keitaroCampaignId: number) =>
      unwrap(
        api.POST('/api/v1/campaigns/import', {
          body: { keitaro_campaign_id: keitaroCampaignId },
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.campaigns.all })
    },
  })
}
