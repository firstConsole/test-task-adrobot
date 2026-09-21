import type { Campaign } from './types'

/**
 * Whether this campaign's setup can be finished, which is whether we know what it should be.
 *
 * Only a campaign built here remembers the country and the offer part 1 was asked for, and
 * only those two describe the flows that are missing. For an imported half-built campaign
 * the answer is no — its flows can be edited, but rebuilding them would mean guessing what
 * its author meant. The API refuses that with a 409, and this is the same rule, asked before
 * the button is drawn rather than after it is pressed.
 */
export function canFinishSetup(campaign: Campaign): boolean {
  if (campaign.setup_status === 'ready') return false

  return (campaign.requested_country ?? null) !== null && (campaign.requested_offer_id ?? null) !== null
}
