import { infiniteQueryOptions, keepPreviousData, useInfiniteQuery } from '@tanstack/react-query'

import { api, unwrap } from '@/shared/api/client'
import { queryKeys } from '@/shared/api/query-keys'

/** One screenful. The cursor is what continues it, so this is a page size and not a cap. */
const PAGE_SIZE = 20

/**
 * The campaigns this service knows about, newest first, narrowed by a free-text term.
 *
 * Keyset and not offset: the cursor encodes the exact `(created_at, id)` the index is on, so
 * a campaign created while somebody is reading page two does not shift page three under
 * them. The token is opaque on purpose — the client hands back what it was given, which is
 * what keeps the sort order from becoming part of this API's contract.
 *
 * `after` is deliberately absent from the query key: a page is not a separate cache entry,
 * it is more of this one.
 */
export function campaignsQuery(term: string) {
  const q = term.trim()

  return infiniteQueryOptions({
    queryKey: queryKeys.campaigns.list({ q: q === '' ? undefined : q, limit: PAGE_SIZE }),
    queryFn: ({ pageParam, signal }) =>
      unwrap(
        api.GET('/api/v1/campaigns', {
          params: {
            query: { q: q === '' ? undefined : q, limit: PAGE_SIZE, after: pageParam ?? undefined },
          },
          signal,
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? null,
    // The list on screen stays put while a new search term is fetched, marked stale rather
    // than blanked — the same reason the offer combobox keeps its previous answer.
    placeholderData: keepPreviousData,
  })
}

export function useCampaigns(term: string) {
  return useInfiniteQuery(campaignsQuery(term))
}
