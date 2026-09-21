import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useParams } from 'react-router'
import { describe, expect, it } from 'vitest'

import { ROUTES, campaignStreamsPath } from '@/shared/config/routes'
import type { Answer, ApiCall } from '@/shared/test/api-double'
import { problem, serveApi } from '@/shared/test/api-double'
import { renderAtRoutes } from '@/shared/test/render'

import { CreateCampaignForm } from './create-campaign-form'

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
  requested_offer_id: 11112,
}

const OFFERS = {
  offers: [{ id: 11112, name: 'Oxys', state: 'active', country: ['PL', 'ES'] }],
}

/** The editor, as far as this screen is concerned: proof the router arrived, and its id. */
function EditorStub() {
  const { campaignId } = useParams<'campaignId'>()
  return <p>editor of {campaignId}</p>
}

function render(answer: (call: ApiCall) => Answer): ApiCall[] {
  const calls = serveApi((call) => (call.path === '/api/v1/offers' ? { body: OFFERS } : answer(call)))

  renderAtRoutes(
    [
      { path: ROUTES.campaignCreate, element: <CreateCampaignForm /> },
      { path: ROUTES.campaignStreams, element: <EditorStub /> },
    ],
    ROUTES.campaignCreate,
  )

  return calls
}

async function fillIn(user: ReturnType<typeof userEvent.setup>) {
  // Spaces around the name on purpose: the schema trims, and the body below proves it.
  await user.type(screen.getByLabelText('Имя'), '  Summer MX  ')
  await user.click(screen.getByLabelText('Гео'))
  await user.type(screen.getByPlaceholderText('Поиск по коду или названию…'), 'mexi')
  await user.click(await screen.findByText('Mexico'))
  await user.click(screen.getByLabelText('Оффер'))
  await user.click(await screen.findByText('Oxys'))
}

describe('the campaign form', () => {
  it('refuses an empty form without asking the server', async () => {
    const user = userEvent.setup()
    const calls = render(() => ({ status: 201, body: CAMPAIGN }))

    await user.click(screen.getByRole('button', { name: 'CREATE' }))

    expect(await screen.findAllByRole('alert')).toHaveLength(3)
    expect(calls).toHaveLength(0)
  })

  it('sends the three fields as the body, and opens what came back', async () => {
    const user = userEvent.setup()
    const calls = render(() => ({ status: 201, body: CAMPAIGN }))

    await fillIn(user)
    await user.click(screen.getByRole('button', { name: 'CREATE' }))

    expect(await screen.findByText(`editor of ${CAMPAIGN.id}`)).toBeInTheDocument()
    expect(calls.at(-1)?.body).toEqual({ name: 'Summer MX', country: 'MX', offer_id: 11112 })
    // The tracker, one click away, and its address arriving on the campaign rather than
    // from anything in this bundle.
    expect(await screen.findByRole('link', { name: 'VIEW IN KT' })).toHaveAttribute(
      'href',
      CAMPAIGN.tracker_url,
    )
  })

  it('opens a campaign whose flows the tracker refused, and says which', async () => {
    const user = userEvent.setup()
    render(() => ({
      status: 201,
      body: {
        ...CAMPAIGN,
        setup_status: 'needs_attention',
        setup_failure: 'Keitaro refused Flow 2: offer 11112 is archived.',
      },
    }))

    await fillIn(user)
    await user.click(screen.getByRole('button', { name: 'CREATE' }))

    expect(await screen.findByText(`editor of ${CAMPAIGN.id}`)).toBeInTheDocument()
    expect(
      await screen.findByText('Summer MX создана, но её потоки не достроены.'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('Keitaro refused Flow 2: offer 11112 is archived.'),
    ).toBeInTheDocument()
  })

  it('puts a refusal under the field it names, and the rest above the button', async () => {
    const user = userEvent.setup()
    render(() => ({
      status: 422,
      body: problem(422, 'The request was refused.', {
        errors: [
          { location: 'body.name', message: 'A campaign called that already exists.' },
          { location: 'tracker.offer_id', message: 'offer 11112 is archived' },
          { location: 'query.weird', message: 'nobody asked for this' },
        ],
        correlation_id: 'corr-1',
      }),
    }))

    await fillIn(user)
    await user.click(screen.getByRole('button', { name: 'CREATE' }))

    expect(await screen.findByText('A campaign called that already exists.')).toBeInTheDocument()
    // Labelled, because a complaint from the tracker is a different kind of statement.
    expect(screen.getByText('Keitaro: offer 11112 is archived')).toBeInTheDocument()
    // A location this form has no input for is still said out loud, with the id to quote.
    expect(screen.getByText('query.weird: nobody asked for this (corr-1)')).toBeInTheDocument()
    expect(screen.getByLabelText('Оффер')).toHaveAttribute('aria-invalid', 'true')
    await waitFor(() => {
      expect(screen.getByLabelText('Имя')).toHaveFocus()
    })
    // Nothing was created, so nothing was opened.
    expect(screen.queryByText(new RegExp(campaignStreamsPath(CAMPAIGN.id)))).toBeNull()
  })

  it('warns when the offer is set up for the one geo Flow 1 takes away', async () => {
    const user = userEvent.setup()
    serveApi(() => ({ body: { offers: [{ ...OFFERS.offers[0], country: ['MX'] }] } }))

    renderAtRoutes(
      [{ path: ROUTES.campaignCreate, element: <CreateCampaignForm /> }],
      ROUTES.campaignCreate,
    )

    await user.click(screen.getByLabelText('Гео'))
    await user.type(screen.getByPlaceholderText('Поиск по коду или названию…'), 'mexi')
    await user.click(await screen.findByText('Mexico'))
    await user.click(screen.getByLabelText('Оффер'))
    await user.click(await screen.findByText('Oxys'))

    expect(await screen.findByText(/100% ничего/)).toBeInTheDocument()
    // A warning, not a refusal: the button stays live.
    expect(screen.getByRole('button', { name: 'CREATE' })).toBeEnabled()
  })
})
