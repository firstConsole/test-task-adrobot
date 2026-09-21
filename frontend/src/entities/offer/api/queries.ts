import { keepPreviousData, queryOptions, useQuery } from '@tanstack/react-query'

import { api, unwrap } from '@/shared/api/client'
import { queryKeys } from '@/shared/api/query-keys'

/** How many offers one dropdown shows. The catalogue is searched, not scrolled. */
const PAGE_SIZE = 20

/**
 * The offer catalogue, searched on the server by id prefix and by name.
 *
 * `keepPreviousData` is what stops the list blanking on every keystroke: the previous
 * answer stays on screen, marked as stale, until the next one arrives. The `signal` is the
 * other half — a query that is no longer observed aborts its request instead of racing the
 * one that replaced it.
 */
export function offerSearchQuery(term: string, options: { enabled: boolean }) {
  const q = term.trim()

  return queryOptions({
    queryKey: queryKeys.offers.search(q),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET('/api/v1/offers', {
          params: { query: { q: q === '' ? undefined : q, limit: PAGE_SIZE } },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
    enabled: options.enabled,
  })
}

export function useOfferSearch(term: string, options: { enabled: boolean }) {
  return useQuery(offerSearchQuery(term, options))
}
