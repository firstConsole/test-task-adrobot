import type { CampaignStats, OfferStats } from '../model/types'

/**
 * One campaign's numbers, keyed for lookup.
 *
 * Built once, at the top of the screen, and read `O(1)` per cell. The alternative — a cell
 * that fetches what it needs — is the N+1 this table would make most tempting: forty rows,
 * forty requests, and a report builder asked the same question forty times.
 */
export type CampaignNumbers = {
  day: string
  timezone: string
  available: boolean
  stale: boolean
  unavailableReason: string | null
  clicksByStream: ReadonlyMap<number, number>
  byOffer: ReadonlyMap<number, OfferStats>
}

export function campaignNumbers(stats: CampaignStats): CampaignNumbers {
  return {
    day: stats.day,
    timezone: stats.timezone,
    available: stats.available,
    stale: stats.stale,
    unavailableReason: stats.unavailable_reason ?? null,
    clicksByStream: new Map(stats.streams.map((row) => [row.keitaro_stream_id, row.clicks])),
    byOffer: new Map(stats.offers.map((row) => [row.offer_id, row])),
  }
}

/** What the screen shows when the numbers could not be read at all. */
export function unreadableNumbers(reason: string): CampaignNumbers {
  return {
    day: '',
    timezone: '',
    available: false,
    stale: false,
    unavailableReason: reason,
    clicksByStream: new Map(),
    byOffer: new Map(),
  }
}
