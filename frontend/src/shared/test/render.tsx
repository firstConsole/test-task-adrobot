import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement, ReactNode } from 'react'
import { RouterProvider, createMemoryRouter } from 'react-router'

import { Toaster } from '@/shared/ui/sonner'

/**
 * The app's providers, with the retries off.
 *
 * Retrying is right in the browser and wrong here: a test that asserts what a 502 does would
 * otherwise assert it twice and take a second longer to fail.
 */
export function renderWithQuery(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: 0 } },
  })

  function Providers({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }

  return { queryClient, ...render(ui, { wrapper: Providers }) }
}

/** A `<tbody>` needs a table around it, and a row needs a body. */
export function inTable(children: ReactNode) {
  return <table>{children}</table>
}

/** One row, for the components that render a `<tr>` rather than a whole group. */
export function inTableBody(children: ReactNode) {
  return (
    <table>
      <tbody>{children}</tbody>
    </table>
  )
}

/**
 * A page, mounted at a route, with the toaster the app layout normally provides.
 *
 * Whole-page rather than component-by-component where the thing under test is the cache:
 * an optimistic update is written to the query client and read back by whatever renders from
 * it, so a component handed its data as a prop could never show the rollback.
 */
export function renderAtRoute(element: ReactElement, path: string, at: string) {
  const router = createMemoryRouter([{ path, element }], { initialEntries: [at] })

  return renderWithQuery(
    <>
      <RouterProvider router={router} />
      <Toaster />
    </>,
  )
}
