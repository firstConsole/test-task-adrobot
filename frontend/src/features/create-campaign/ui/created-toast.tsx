import { toast } from 'sonner'

import type { components } from '@/shared/api/schema.gen'

type Campaign = components['schemas']['CampaignResponse']

/** Long enough to read the numbers and click through to the tracker, and then gone. */
const LINGER_MS = 12_000

/**
 * Say what was built, and offer the tracker as the place to check it.
 *
 * The link is an anchor and not a button that opens a window: a reviewer wants to
 * middle-click it into a second tab beside this one, and `window.open` takes that away. Its
 * address comes from the campaign — which tracker this service wraps is configuration the
 * frontend is never told, so a link to Keitaro can only ever arrive from the API.
 *
 * Two outcomes, because 201 is not always a whole campaign: the tracker can accept a
 * campaign and then refuse one of its flows, and `setup_status` is the only thing that can
 * tell the two apart — the campaign is in Keitaro either way.
 */
export function campaignCreatedToast(campaign: Campaign, country: string): void {
  const link = (
    <a
      href={campaign.tracker_url}
      target="_blank"
      rel="noreferrer"
      className="underline underline-offset-2"
    >
      VIEW IN KT
    </a>
  )

  if (campaign.setup_status === 'ready') {
    toast.success(`${campaign.name} — в Keitaro.`, {
      description: `Кампания ${String(campaign.keitaro_campaign_id)}: Flow 1 ловит ${country} и уводит на google.com, Flow 2 крутит один оффер на 100%.`,
      action: link,
      duration: LINGER_MS,
    })
    return
  }

  toast.warning(`${campaign.name} создана, но её потоки не достроены.`, {
    description:
      campaign.setup_failure ??
      'Трекер принял кампанию и отказал потоку. FETCH STREAMS FROM KT покажет, что у него есть.',
    action: link,
    duration: LINGER_MS,
  })
}
