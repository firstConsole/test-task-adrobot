/**
 * The editor's business object, named once.
 *
 * Every type here is an alias onto the generated schema rather than a hand-written mirror
 * of it: the names below are what this slice talks about, and the shapes stay whatever the
 * backend last published. A field that disappears from the API fails `npm run typecheck`
 * instead of rendering `undefined` on somebody's screen.
 */

import type { components } from '@/shared/api/schema.gen'

/** The whole editor screen in one answer: the campaign, and its flows already ordered. */
export type CampaignStreams = components['schemas']['StreamsResponse']

/** One flow, with its rows, its draft and the server's verdict on pushing it. */
export type Stream = components['schemas']['StreamResponse']

/** One line of a flow's offer table. */
export type StreamRow = components['schemas']['StreamRowResponse']

/** One condition on a flow — `country accept ["AU"]` in the reference campaign. */
export type StreamFilter = components['schemas']['StreamFilterResponse']
