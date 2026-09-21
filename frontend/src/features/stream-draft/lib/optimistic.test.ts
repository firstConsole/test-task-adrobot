import { describe, expect, it } from 'vitest'

import { aRow, aStream } from '@/shared/test/factories'

import { withOperation, withPin, withStream } from './optimistic'

describe('an optimistic edit', () => {
  const flow = aStream({
    rows: [
      aRow({ offer_id: 3749, share: 38 }),
      aRow({ offer_id: 3717, share: 25, pinned_share: 25 }),
      aRow({ offer_id: 11111, share: 0, removed: true }),
    ],
  })

  it('blanks the share of every row it cannot work out, and only those', () => {
    const next = withOperation(flow, { kind: 'remove', offerId: 3749 })
    const shares = Object.fromEntries(next.rows.map((row) => [row.offer_id, row.share]))

    expect(shares).toEqual({
      // Struck out, so it takes nothing — the client knows that much.
      3749: 0,
      // Held where it was pinned, which is also known.
      3717: 25,
      // Already out of the division.
      11111: 0,
    })
  })

  it('leaves the free rows to the server and marks them as unknown', () => {
    const next = withOperation(flow, { kind: 'bring_back', offerId: 11111 })
    const shares = Object.fromEntries(next.rows.map((row) => [row.offer_id, row.share]))

    // Two free rows now, and no client-side arithmetic anywhere that could fill them in.
    expect(shares).toEqual({ 3749: null, 3717: 25, 11111: null })
  })

  it('marks the flow dirty, and leaves can_push to the server', () => {
    const next = withOperation(flow, { kind: 'remove', offerId: 3749 })

    expect(next.dirty).toBe(true)
    expect(next.can_push).toBe(false)
  })

  it('slots an added row above the struck-out ones and sorts nothing', () => {
    const next = withOperation(flow, {
      kind: 'add',
      offerId: 13972,
      offer: { id: 13972, name: 'another test offer 2', state: 'active', country: [] },
    })

    expect(next.rows.map((row) => row.offer_id)).toEqual([3749, 3717, 13972, 11111])
  })
})

describe('a pin', () => {
  const flow = aStream({
    rows: [aRow({ offer_id: 3749, share: 38 }), aRow({ offer_id: 3717, share: 25 })],
  })

  it('moves no share and does not make the flow dirty', () => {
    const next = withPin(flow, 3717, true)

    expect(next.rows.map((row) => row.share)).toEqual([38, 25])
    expect(next.rows.map((row) => row.pinned_share)).toEqual([null, 25])
    expect(next.dirty).toBe(false)
  })

  it('releases the row without touching any other', () => {
    const held = withPin(flow, 3717, true)
    const released = withPin(held, 3717, false)

    expect(released.rows.map((row) => row.pinned_share)).toEqual([null, null])
    expect(released.rows.map((row) => row.share)).toEqual([38, 25])
  })
})

describe('the screen', () => {
  it('replaces one flow and leaves the rest of the campaign alone', () => {
    const view = {
      campaign: { id: 'c' } as never,
      streams: [aStream({ keitaro_stream_id: 564220, name: 'Flow 1' }), aStream()],
    }
    const edited = aStream({ dirty: true })

    const next = withStream(view, edited)

    expect(next.streams[0]?.name).toBe('Flow 1')
    expect(next.streams[1]?.dirty).toBe(true)
  })
})
