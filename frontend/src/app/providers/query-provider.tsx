import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { ApiError } from '@/shared/api/client'

/**
 * A GET is tried twice; a mutation is never retried, because every mutation here writes to
 * somebody's live tracker and a repeated POST is how two campaigns get made. A refusal the
 * API already decided on — 401, 404, 409, 422 — is not retried either: the second answer
 * would be the first one again.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) =>
        failureCount < 1 && !(error instanceof ApiError && error.status < 500),
    },
    mutations: { retry: 0 },
  },
})

export function QueryProvider({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}
