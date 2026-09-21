import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useParams } from 'react-router'
import { describe, expect, it } from 'vitest'

import { ROUTES } from '@/shared/config/routes'
import type { Answer, ApiCall } from '@/shared/test/api-double'
import { problem, serveApi } from '@/shared/test/api-double'
import { renderAtRoutes } from '@/shared/test/render'

import { CampaignListPage } from './campaign-list-page'

const CAMPAIGN = {
  id: '4a34da83-ddca-4190-851c-dc64dcf4cc7b',
  keitaro_campaign_id: 93212,
  alias: 'wVqN1R',
  name: 'Summer MX',
  state: 'active',
  setup_status: 'ready',
  tracker_url: 'https://tracker.example/admin/#/campaigns/93212',
  created_at: '2026-09-21T12:00:00Z',
  requested_country: 'MX',
}

function EditorStub() {
  const { campaignId } = useParams<'campaignId'>()
  return <p>editor of {campaignId}</p>
}

function render(answer: (call: ApiCall) => Answer): ApiCall[] {
  const calls = serveApi(answer)

  renderAtRoutes(
    [
      { path: ROUTES.campaignList, element: <CampaignListPage /> },
      { path: ROUTES.campaignStreams, element: <EditorStub /> },
    ],
    ROUTES.campaignList,
  )

  return calls
}

describe('the campaign list', () => {
  it('asks for more with the cursor it was given, and searches on the server', async () => {
    const user = userEvent.setup()
    const calls = render((call) => {
      if (call.search.get('q') === 'nothing') return { body: { campaigns: [] } }
      if (call.search.get('after') === 'cursor-1') {
        return { body: { campaigns: [{ ...CAMPAIGN, id: 'older', name: 'Older' }] } }
      }
      return { body: { campaigns: [CAMPAIGN], next_cursor: 'cursor-1' } }
    })

    expect(await screen.findByRole('link', { name: 'Summer MX' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'LOAD MORE' }))

    expect(await screen.findByRole('link', { name: 'Older' })).toBeInTheDocument()
    expect(calls.at(-1)?.search.get('after')).toBe('cursor-1')
    // Still there: a page is more of this list, not a different one.
    expect(screen.getByRole('link', { name: 'Summer MX' })).toBeInTheDocument()

    await user.type(screen.getByLabelText('Search campaigns'), 'nothing')

    expect(await screen.findByText(/No campaign here matches/)).toBeInTheDocument()
    expect(calls.at(-1)?.search.get('q')).toBe('nothing')
  })
})

describe('opening a campaign the tracker already has', () => {
  it('opens the copy that is already here instead of refusing', async () => {
    const user = userEvent.setup()
    render((call) =>
      call.method === 'POST'
        ? {
            status: 409,
            // The 409 this API was built to answer with: it names the copy that exists.
            body: problem(409, 'That campaign is already open here.', { campaign_id: CAMPAIGN.id }),
          }
        : { body: { campaigns: [] } },
    )

    await user.type(await screen.findByLabelText('Open one from Keitaro'), '93212')
    await user.click(screen.getByRole('button', { name: 'IMPORT' }))

    expect(await screen.findByText(`editor of ${CAMPAIGN.id}`)).toBeInTheDocument()
  })

  it("puts the tracker's own no under the field", async () => {
    const user = userEvent.setup()
    render((call) =>
      call.method === 'POST'
        ? { status: 404, body: problem(404, 'Keitaro has no campaign 1.') }
        : { body: { campaigns: [] } },
    )

    await user.type(await screen.findByLabelText('Open one from Keitaro'), '1')
    await user.click(screen.getByRole('button', { name: 'IMPORT' }))

    expect(await screen.findByText(/Keitaro has no campaign 1\./)).toBeInTheDocument()
  })

  it('never sends an id that is not a number', async () => {
    const user = userEvent.setup()
    const calls = render(() => ({ body: { campaigns: [] } }))

    await user.type(await screen.findByLabelText('Open one from Keitaro'), 'abc')
    await user.click(screen.getByRole('button', { name: 'IMPORT' }))

    expect(await screen.findByText(/A campaign id is the number/)).toBeInTheDocument()
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)
  })
})
