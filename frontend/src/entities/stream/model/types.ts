/**
 * The editor's business object, named once.
 *
 * Every type here is an alias onto the generated schema rather than a hand-written mirror of
 * it: the names below are what this slice talks about, and the shapes stay whatever the
 * backend last published. A field that disappears from the API fails `npm run typecheck`
 * instead of rendering `undefined` on somebody's screen.
 *
 * The one widening is `share`. On the wire it is always a number; in this cache it is
 * `number | null`, because an optimistic edit knows the structure of the next state but not
 * its arithmetic — and `null` is how a cell is told to draw a skeleton rather than a stale
 * percentage. Nothing is ever sent back in this shape, so the API contract is untouched.
 */

import type { components } from '@/shared/api/schema.gen'

/** One line of a flow's offer table. `share` is null while the server is still dividing. */
export type StreamRow = Omit<components['schemas']['StreamRowResponse'], 'share'> & {
  share: number | null
}

/** One flow, with its rows, its draft and the server's verdict on pushing it. */
export type Stream = Omit<components['schemas']['StreamResponse'], 'rows'> & {
  rows: StreamRow[]
}

/** The whole editor screen in one answer: the campaign, and its flows already ordered. */
export type CampaignStreams = Omit<components['schemas']['StreamsResponse'], 'streams'> & {
  streams: Stream[]
}

/** One condition on a flow — `country accept ["AU"]` in the reference campaign. */
export type StreamFilter = components['schemas']['StreamFilterResponse']

/** One flow as the API answers for it, which every edit hands back. */
export type StreamAnswer = components['schemas']['StreamResponse']
