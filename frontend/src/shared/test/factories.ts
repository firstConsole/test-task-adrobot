import type { Offer } from '@/entities/offer'
import type { Stream, StreamRow } from '@/entities/stream'

/** The reference campaign's own offers, so a failing test reads like the video. */
export function anOffer(offer: Partial<Offer> & { id: number }): Offer {
  return {
    name: `offer ${String(offer.id)}`,
    state: 'active',
    country: ['Romania'],
    affiliate_network: '62',
    preview_url: null,
    ...offer,
  }
}

export function aRow(row: Partial<StreamRow> & { offer_id: number }): StreamRow {
  return {
    offer: anOffer({ id: row.offer_id }),
    share: 0,
    pinned_share: null,
    removed: false,
    ...row,
  }
}

export function aStream(stream: Partial<Stream> = {}): Stream {
  return {
    keitaro_stream_id: 564221,
    name: 'Flow 2',
    schema: 'landings',
    position: 2,
    filters: [],
    absent: false,
    rows: [],
    dirty: false,
    diff: null,
    can_push: false,
    block_reason: null,
    warnings: [],
    ...stream,
  }
}
