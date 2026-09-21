import type { Offer } from '@/entities/offer'
import type { CampaignStreams, Stream, StreamRow } from '@/entities/stream'

/** One edit, as the screen asks for it. `add` carries the offer so the new row has a label. */
export type DraftOperation =
  | { kind: 'add'; offerId: number; offer: Offer }
  | { kind: 'remove'; offerId: number }
  | { kind: 'bring_back'; offerId: number }

/**
 * One row, with every number the client cannot know blanked out.
 *
 * **Optimism in the structure, pessimism in the numbers.** What the client knows it applies
 * at once: a struck-out row takes nothing, a pinned row is held where it was pinned. How the
 * remainder divides up is `redistribute()`'s answer and nobody else's, so it becomes `null`
 * and the cell draws a skeleton until the server says. That is how this screen answers
 * instantly without carrying a second implementation of the arithmetic in TypeScript.
 */
function unsettled(row: StreamRow): StreamRow {
  if (row.removed) return { ...row, share: 0 }
  if (row.pinned_share !== null && row.pinned_share !== undefined) {
    return { ...row, share: row.pinned_share }
  }
  return { ...row, share: null }
}

function edited(rows: readonly StreamRow[], operation: DraftOperation): StreamRow[] {
  const held = rows.some((row) => row.offer_id === operation.offerId)

  switch (operation.kind) {
    case 'add': {
      // A flow already holding this offer refuses the add, removed rows included — so there
      // is nothing to show in advance, only a 409 to report.
      if (held) return [...rows]

      const added: StreamRow = {
        offer_id: operation.offerId,
        offer: operation.offer,
        share: null,
        pinned_share: null,
        removed: false,
      }
      // Slotted above the struck-out rows rather than sorted into place: the order is the
      // server's — active by descending share — and it arrives with the shares a moment later.
      const firstRemoved = rows.findIndex((row) => row.removed)
      return firstRemoved === -1
        ? [...rows, added]
        : [...rows.slice(0, firstRemoved), added, ...rows.slice(firstRemoved)]
    }

    case 'remove':
      return rows.map((row) => (row.offer_id === operation.offerId ? { ...row, removed: true } : row))

    case 'bring_back':
      return rows.map((row) => (row.offer_id === operation.offerId ? { ...row, removed: false } : row))
  }
}

/**
 * The flow as it will look the instant the button is pressed.
 *
 * `dirty` is set here because the client does know it: an edit has been staged. `can_push` is
 * not — whether this flow may be written to the tracker is the server's verdict, and offering
 * a button it would refuse is worse than showing it a moment late.
 */
export function withOperation(stream: Stream, operation: DraftOperation): Stream {
  return { ...stream, rows: edited(stream.rows, operation).map(unsettled), dirty: true }
}

/**
 * The flow with one row held, or let go.
 *
 * Nothing else changes — not another row's share, not `dirty`. A pin says where the *next*
 * division must leave this row; it is not a division, so there is nothing here to guess at.
 */
export function withPin(stream: Stream, offerId: number, pin: boolean): Stream {
  return {
    ...stream,
    rows: stream.rows.map((row) =>
      row.offer_id === offerId ? { ...row, pinned_share: pin ? row.share : null } : row,
    ),
  }
}

/** The screen with one flow swapped out, which is how every answer of an edit lands. */
export function withStream(view: CampaignStreams, stream: Stream): CampaignStreams {
  return {
    ...view,
    streams: view.streams.map((flow) =>
      flow.keitaro_stream_id === stream.keitaro_stream_id ? stream : flow,
    ),
  }
}
