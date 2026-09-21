import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { StreamAnswer } from '@/entities/stream'
import { ROUTES } from '@/shared/config/routes'
import { problem, serveApi } from '@/shared/test/api-double'
import { renderAtRoute } from '@/shared/test/render'

import { CampaignStreamsPage } from './campaign-streams-page'

const CAMPAIGN = '4a34da83-ddca-4190-851c-dc64dcf4cc7b'

const CAMPAIGN_BODY = {
  id: CAMPAIGN,
  keitaro_campaign_id: 93212,
  alias: 'wVqN1R',
  name: 'campaign 2',
  state: 'active',
  setup_status: 'ready',
  tracker_url: 'https://tracker.example/admin/#/campaigns/93212',
  synced_at: '2026-09-21T12:00:00Z',
  created_at: '2026-09-21T12:00:00Z',
}

// Typed as the API's own answer, so a fixture that leaves a field out is a compile error
// rather than a crash three components deep.
function flow(rows: StreamAnswer['rows'], extra: Partial<StreamAnswer> = {}): StreamAnswer {
  return {
    keitaro_stream_id: 564221,
    name: 'Flow 2',
    schema: 'landings',
    position: 2,
    filters: [],
    absent: false,
    rows,
    dirty: false,
    diff: null,
    can_push: false,
    block_reason: null,
    warnings: [],
    ...extra,
  }
}

const CLEAN_ROWS: StreamAnswer['rows'] = [
  { offer_id: 3749, offer: { id: 3749, name: 'Miaflow 0009', state: 'active', country: [] }, share: 62, pinned_share: null, removed: false },
  { offer_id: 3717, offer: { id: 3717, name: 'Miaflow 0008', state: 'active', country: [] }, share: 38, pinned_share: null, removed: false },
]

const STATS = { day: '2026-09-21', timezone: 'UTC', available: true, stale: false, streams: [], offers: [] }

function renderEditor() {
  return renderAtRoute(<CampaignStreamsPage />, ROUTES.campaignStreams, `/campaigns/${CAMPAIGN}`)
}

describe('an edit that the server refuses', () => {
  it('shows what the client knows at once, then puts it all back and says why', async () => {
    const user = userEvent.setup()
    let release!: () => void
    const held = new Promise<void>((resolve) => {
      release = resolve
    })

    serveApi(async (call) => {
      if (call.path.endsWith('/draft/operations')) {
        // Held open so the optimistic state can be asserted before the answer lands.
        await held
        return { status: 502, body: problem(502, 'Keitaro did not answer.') }
      }
      if (call.path.endsWith('/stats')) return { body: STATS }
      return { body: { campaign: CAMPAIGN_BODY, streams: [flow(CLEAN_ROWS)] } }
    })

    renderEditor()
    await screen.findByRole('row', { name: /Miaflow 0008 in Flow 2$/ })

    await user.click(screen.getByRole('button', { name: /remove Miaflow 0008/i }))

    // Optimism in the structure: the row is struck out and takes nothing, at once.
    const struck = await screen.findByRole('row', { name: /Miaflow 0008 in Flow 2, removed/ })
    expect(within(struck).getByText('(removed)')).toBeInTheDocument()
    expect(within(struck).getByText('0%')).toBeInTheDocument()

    // Pessimism in the numbers: the free row's new share is the server's to work out, so it
    // is drawn as a skeleton rather than guessed at here.
    expect(screen.getByLabelText(/working out the share of Miaflow 0009/i)).toBeInTheDocument()
    expect(screen.queryByText('62%')).not.toBeInTheDocument()

    release()

    await waitFor(() => {
      expect(screen.queryByText('(removed)')).not.toBeInTheDocument()
    })
    expect(screen.getByText('62%')).toBeInTheDocument()
    expect(await screen.findByText('Keitaro did not answer.')).toBeInTheDocument()
  })
})

describe('a push onto a flow that moved', () => {
  it('opens the two readings side by side instead of reporting an error', async () => {
    const user = userEvent.setup()

    serveApi((call) => {
      if (call.path.endsWith('/draft/push')) {
        return {
          status: 409,
          body: problem(409, 'flow 564221 has been edited in Keitaro since this draft was opened', {
            conflict: {
              tracker_holds: [
                { offer_id: 3749, share: 38, state: 'active' },
                { offer_id: 3717, share: 25, state: 'active' },
              ],
              push_would_write: [
                { offer_id: 3749, share: 50, state: 'active' },
                { offer_id: 3717, share: 50, state: 'active' },
              ],
            },
          }),
        }
      }
      if (call.path.endsWith('/stats')) return { body: STATS }
      return {
        body: {
          campaign: CAMPAIGN_BODY,
          streams: [
            flow(CLEAN_ROWS, {
              dirty: true,
              can_push: true,
              diff: {
                added: [],
                removed: [],
                brought_back: [],
                share_changes: [{ offer_id: 3717, was: 38, now: 50 }],
                desired: [
                  { offer_id: 3749, share: 50, state: 'active' },
                  { offer_id: 3717, share: 50, state: 'active' },
                ],
              },
            }),
          ],
        },
      }
    })

    renderEditor()
    await user.click(await screen.findByRole('button', { name: /push flow 2 to keitaro/i }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Flow 2 has moved in Keitaro/i)).toBeInTheDocument()

    // Both readings, lined up by offer, so there is something to decide between.
    const row = within(dialog).getByRole('row', { name: /3749/ })
    expect(within(row).getByText('38%')).toBeInTheDocument()
    expect(within(row).getByText('50%')).toBeInTheDocument()

    expect(within(dialog).getByRole('button', { name: /overwrite keitaro/i })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: /leave it/i })).toBeInTheDocument()
  })
})

describe('a campaign whose flows the tracker never took', () => {
  const UNFINISHED = {
    ...CAMPAIGN_BODY,
    setup_status: 'needs_attention',
    setup_failure: 'Keitaro refused Flow 2.',
    requested_country: 'MX',
    requested_offer_id: 11112,
  }

  function serveCampaign(campaign: Record<string, unknown>) {
    return serveApi((call) => {
      if (call.path.endsWith('/repair')) {
        return { body: { ...campaign, setup_status: 'ready', setup_failure: null } }
      }
      if (call.path.endsWith('/stats')) return { body: STATS }
      return { body: { campaign, streams: [] } }
    })
  }

  it('offers to finish the ones we built, and says when it did', async () => {
    const user = userEvent.setup()
    const calls = serveCampaign(UNFINISHED)

    renderEditor()
    expect(await screen.findByText(/Keitaro refused Flow 2\./)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'FINISH SETUP' }))

    await waitFor(() => {
      expect(calls.some((call) => call.path.endsWith('/repair'))).toBe(true)
    })
    expect(await screen.findByText('Flow 1 and Flow 2 are both in Keitaro now.')).toBeInTheDocument()
  })

  it('does not offer the button on a campaign it would be refused for', async () => {
    // Imported: nothing here remembers what its flows were meant to be, so there is nothing
    // to rebuild them from — and the API answers that with a 409.
    serveCampaign({ ...UNFINISHED, requested_country: null, requested_offer_id: null })

    renderEditor()

    expect(await screen.findByText(/It was built somewhere else/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'FINISH SETUP' })).toBeNull()
  })
})
