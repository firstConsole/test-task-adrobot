import { vi } from 'vitest'

export type ApiCall = {
  method: string
  path: string
  search: URLSearchParams
  body: unknown
}

export type Answer = { status?: number; body?: unknown }

/**
 * A second implementation of this app's one server, small enough to read.
 *
 * Everything the frontend sends goes through `openapi-fetch` to the global `fetch`, so a
 * function that answers it is the whole of what a test needs — no service worker, no handler
 * registry, nothing to configure. The backend's suite keeps a second implementation of its
 * ports for the same reason: a double you can read beats a framework you have to trust.
 *
 * The returned array is every call in order, which is how a test asserts that a component
 * made no request at all.
 */
export function serveApi(route: (call: ApiCall) => Answer | Promise<Answer>): ApiCall[] {
  const calls: ApiCall[] = []

  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init)
    const url = new URL(request.url, 'http://localhost')
    const text = await request.clone().text()

    const call: ApiCall = {
      method: request.method,
      path: url.pathname,
      search: url.searchParams,
      body: text === '' ? undefined : (JSON.parse(text) as unknown),
    }
    calls.push(call)

    const answer = await route(call)
    return new Response(answer.body === undefined ? null : JSON.stringify(answer.body), {
      status: answer.status ?? 200,
      headers: { 'content-type': 'application/json' },
    })
  })

  return calls
}

/** The body this API answers a refusal with, in the one shape the client unwraps. */
export function problem(status: number, detail: string, extra: Record<string, unknown> = {}) {
  return {
    type: `urn:adrobot:problem:test-${String(status)}`,
    title: 'Refused',
    status,
    detail,
    code: `test-${String(status)}`,
    ...extra,
  }
}
