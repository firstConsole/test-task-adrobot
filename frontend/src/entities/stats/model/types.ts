import type { components } from '@/shared/api/schema.gen'

/** One campaign's numbers for one day, with everything needed to caption them. */
export type CampaignStats = components['schemas']['CampaignStatsResponse']

/** What one offer did today — one cell of the Stats column. */
export type OfferStats = components['schemas']['OfferStatsResponse']
