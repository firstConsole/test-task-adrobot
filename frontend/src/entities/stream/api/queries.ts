import { queryOptions, useQuery } from '@tanstack/react-query'

import { api, unwrap } from '@/shared/api/client'
import { queryKeys } from '@/shared/api/query-keys'

/**
 * One campaign's flows, which is the whole editor screen.
 *
 * Exported as options and not only as a hook: the mutations of `features/stream-draft` write
 * the answer of a push straight into this cache entry, and a prefetch from the campaign list
 * wants the same key. A second spelling of the key is a second cache.
 */
export function campaignStreamsQuery(campaignId: string) {
  return queryOptions({
    queryKey: queryKeys.campaigns.streams(campaignId),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET('/api/v1/campaigns/{campaign_id}/streams', {
          params: { path: { campaign_id: campaignId } },
          signal,
        }),
      ),
  })
}

export function useCampaignStreams(campaignId: string) {
  return useQuery(campaignStreamsQuery(campaignId))
}
