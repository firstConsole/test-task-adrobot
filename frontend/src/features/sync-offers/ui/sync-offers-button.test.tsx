import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import type { Offer } from '@/entities/offer'
import { OfferCombobox } from '@/entities/offer'
import { ROUTES } from '@/shared/config/routes'
import { serveApi } from '@/shared/test/api-double'
import { renderAtRoute } from '@/shared/test/render'

import { SyncOffersButton } from './sync-offers-button'

const OFFER: Offer = {
  id: 3749,
  name: 'Miaflow [BEAUTY-RO-BE_0009]',
  state: 'active',
  country: ['Romania'],
  affiliate_network: '62',
  preview_url: null,
}

/** The combobox as both screens mount it: with somewhere to go when it comes back empty. */
function Picker() {
  const [value, setValue] = useState<Offer | null>(null)

  return <OfferCombobox value={value} onChange={setValue} empty={<SyncOffersButton />} />
}

describe('fetching the offer catalogue', () => {
  it('reads the tracker and says how much came back', async () => {
    const user = userEvent.setup()
    const calls = serveApi(() => ({ body: { offers: 3, synced_at: '2026-09-21T13:59:42Z' } }))

    renderAtRoute(<SyncOffersButton />, ROUTES.campaignCreate, ROUTES.campaignCreate)
    await user.click(screen.getByRole('button', { name: /fetch offers from kt/i }))

    await waitFor(() => {
      expect(calls.at(-1)?.path).toBe('/api/v1/offers/sync')
    })
    expect(calls.at(-1)?.method).toBe('POST')
    expect(await screen.findByText('3 offers read from Keitaro.')).toBeInTheDocument()
  })

  it('does not report an emptied catalogue when the tracker listed nothing', async () => {
    const user = userEvent.setup()
    serveApi(() => ({ body: { offers: 0, synced_at: null } }))

    renderAtRoute(<SyncOffersButton />, ROUTES.campaignCreate, ROUTES.campaignCreate)
    await user.click(screen.getByRole('button', { name: /fetch offers from kt/i }))

    expect(
      await screen.findByText('Keitaro listed no offers, so the catalogue was left as it was.'),
    ).toBeInTheDocument()
  })

  it('fills the dropdown it was offered in', async () => {
    // The whole bug this button exists for: a catalogue nothing has ever read answers every
    // search with nothing, and no screen said what to do about it.
    const user = userEvent.setup()
    let synced = false
    serveApi((call) => {
      if (call.path === '/api/v1/offers/sync') {
        synced = true
        return { body: { offers: 1, synced_at: '2026-09-21T13:59:42Z' } }
      }
      return { body: { offers: synced ? [OFFER] : [] } }
    })

    renderAtRoute(<Picker />, ROUTES.campaignCreate, ROUTES.campaignCreate)
    await user.click(screen.getByRole('combobox'))

    // Not "No results found": nothing has been read, and the two are not the same problem.
    expect(await screen.findByText(/the catalogue is empty/i)).toBeInTheDocument()

    await user.click(await screen.findByRole('button', { name: /fetch offers from kt/i }))

    expect(await screen.findByRole('option', { name: /Miaflow/ })).toBeInTheDocument()
  })

  it('leaves the offer alone when the tracker refuses', async () => {
    const user = userEvent.setup()
    const calls = serveApi((call) =>
      call.path === '/api/v1/offers/sync'
        ? { status: 502, body: { status: 502, detail: 'Keitaro did not answer.', code: 'upstream' } }
        : { body: { offers: [] } },
    )
    const onChange = vi.fn()

    renderAtRoute(
      <OfferCombobox value={null} onChange={onChange} empty={<SyncOffersButton />} />,
      ROUTES.campaignCreate,
      ROUTES.campaignCreate,
    )
    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('button', { name: /fetch offers from kt/i }))

    expect(await screen.findByText('Keitaro did not answer.')).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
    expect(calls.filter((call) => call.path === '/api/v1/offers/sync')).toHaveLength(1)
  })
})
