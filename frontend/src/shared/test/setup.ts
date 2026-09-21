import '@testing-library/jest-dom/vitest'

import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

/**
 * What jsdom does not implement and the app insists on.
 *
 * None of this is behaviour under test — it is the browser the components were written
 * against. Left out, the combobox test fails on `hasPointerCapture is not a function`, which
 * says nothing at all about the combobox.
 */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver

// Assigned rather than defaulted: jsdom declares some of these and then throws "not
// implemented" from them, which is worse than not having them.
Object.assign(Element.prototype, {
  hasPointerCapture: () => false,
  setPointerCapture: () => undefined,
  releasePointerCapture: () => undefined,
  scrollIntoView: () => undefined,
})

window.matchMedia = (query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
  addListener: () => undefined,
  removeListener: () => undefined,
  dispatchEvent: () => false,
})

/**
 * `fetch` here is Node's, because jsdom implements none — and Node's `Request` has no
 * document to resolve a relative URL against. The app's client has no `baseUrl` on purpose
 * (the browser only ever calls `/api` on its own origin), so without this every call fails
 * with `Failed to parse URL from /api/v1/...` before it ever reaches the double.
 */
const NodeRequest = globalThis.Request

class SameOriginRequest extends NodeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    super(typeof input === 'string' ? new URL(input, window.location.href) : input, init)
  }
}

globalThis.Request = SameOriginRequest

afterEach(() => {
  cleanup()
})
