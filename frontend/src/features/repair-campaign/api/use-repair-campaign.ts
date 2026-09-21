import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { api, unwrap } from '@/shared/api/client'
import { queryKeys } from '@/shared/api/query-keys'
import { problemMessage } from '@/shared/lib/problem-message'

/**
 * Build whatever of the campaign's two flows the tracker does not have — `FINISH SETUP`.
 *
 * Safe to press twice, and that is a property of the endpoint rather than of this button:
 * it reads the tracker before it writes, leaves a flow that is already there alone, and
 * creates only what is missing. So a campaign whose Flow 1 landed and whose Flow 2 did not
 * gets its Flow 2 and nothing else.
 *
 * The whole campaign key is invalidated and not just the flows: the answer changes
 * `setup_status`, which is what the banner this button sits in is drawn from.
 */
export function useRepairCampaign(campaignId: string) {
  const queryClient = useQueryClient()

  const repair = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/campaigns/{campaign_id}/repair', {
          params: { path: { campaign_id: campaignId } },
        }),
      ),
    onSuccess: async (campaign) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.campaigns.one(campaignId) })

      if (campaign.setup_status === 'ready') {
        toast.success('Flow 1 and Flow 2 are both in Keitaro now.')
        return
      }

      // Pressed, and the tracker refused again. Saying so is the point: the alternative is
      // a button that reports success and a banner that does not go away.
      toast.warning('Keitaro would still not take the whole setup.', {
        description: campaign.setup_failure ?? 'Try again, or build the missing flow by hand.',
      })
    },
    onError: (error) => {
      toast.error(problemMessage(error, 'The setup could not be finished.'))
    },
  })

  return {
    finishing: repair.isPending,
    finishSetup: () => {
      repair.mutate()
    },
  }
}
