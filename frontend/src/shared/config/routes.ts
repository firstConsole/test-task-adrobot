/** The route table and every link built against it, spelled once. */
export const ROUTES = {
  campaignList: '/',
  campaignCreate: '/campaigns/new',
  campaignStreams: '/campaigns/:campaignId',
} as const

/** `campaignId` is our own UUID — the one the API takes, not the tracker's number. */
export function campaignStreamsPath(campaignId: string): string {
  return `/campaigns/${campaignId}`
}
