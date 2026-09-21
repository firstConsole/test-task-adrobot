import { queryOptions, useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'

import { api, unwrap } from '@/shared/api/client'
import { queryKeys } from '@/shared/api/query-keys'
import { problemMessage } from '@/shared/lib/problem-message'

import { campaignNumbers, unreadableNumbers, type CampaignNumbers } from '../lib/numbers'

/** The server caches the report for forty-five seconds; asking faster than that buys nothing. */
const STALE_MS = 30_000

export function campaignStatsQuery(campaignId: string) {
  return queryOptions({
    queryKey: queryKeys.campaigns.stats(campaignId),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET('/api/v1/campaigns/{campaign_id}/stats', {
          params: { path: { campaign_id: campaignId } },
          signal,
        }),
      ),
    staleTime: STALE_MS,
  })
}

/**
 * One request for the whole screen, turned into maps the cells read.
 *
 * `null` means the answer has not arrived; it never means zero. A tracker that would not
 * build the report answers 200 with `available: false` and a reason, because a dark column
 * is not a failed request — the campaign is here and the numbers are the part that is
 * missing. A request that fails outright is folded into the same shape, so the column has
 * one way of being dark rather than two.
 */
export function useCampaignNumbers(campaignId: string): CampaignNumbers | null {
  const stats = useQuery(campaignStatsQuery(campaignId))
  const { data, error } = stats

  return useMemo(() => {
    if (data !== undefined) return campaignNumbers(data)
    if (error !== null) return unreadableNumbers(problemMessage(error, 'The report could not be read.'))
    return null
  }, [data, error])
}
