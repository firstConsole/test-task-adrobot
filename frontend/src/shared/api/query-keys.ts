import type { paths } from './schema.gen'

export type CampaignListQuery = NonNullable<paths['/api/v1/campaigns']['get']['parameters']['query']>

const CAMPAIGNS = ['campaigns'] as const
const OFFERS = ['offers'] as const

/**
 * One factory, and every key below a campaign starts with that campaign — so invalidating
 * `campaigns.one(id)` after a push takes its flows, its draft previews and its statistics
 * with it, which is the whole screen and nothing else.
 */
export const queryKeys = {
  campaigns: {
    all: CAMPAIGNS,
    list: (query: CampaignListQuery) => [...CAMPAIGNS, 'list', query] as const,
    one: (campaignId: string) => [...CAMPAIGNS, campaignId] as const,
    streams: (campaignId: string) => [...CAMPAIGNS, campaignId, 'streams'] as const,
    draftPreview: (campaignId: string, streamId: number) =>
      [...CAMPAIGNS, campaignId, 'streams', streamId, 'draft-preview'] as const,
    stats: (campaignId: string) => [...CAMPAIGNS, campaignId, 'stats'] as const,
  },
  offers: {
    all: OFFERS,
    search: (query: string) => [...OFFERS, 'search', query] as const,
  },
} as const
