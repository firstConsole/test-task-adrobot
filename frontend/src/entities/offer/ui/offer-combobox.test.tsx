import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { serveApi } from '@/shared/test/api-double'
import { renderWithQuery } from '@/shared/test/render'

import type { Offer } from '../model/types'
import { OfferCombobox } from './offer-combobox'

/** The offer the tracker answers `11104` with — its label contains none of those digits. */
const FOUND: Offer = {
  id: 11104,
  name: 'Nutrizen [BEAUTY-CL-BE_0155]',
  state: 'active',
  country: ['Chile'],
  affiliate_network: '62',
  preview_url: null,
}

function Harness({ onPick }: { onPick: (offer: Offer) => void }) {
  const [value, setValue] = useState<Offer | null>(null)

  return (
    <OfferCombobox
      value={value}
      onChange={(offer) => {
        setValue(offer)
        onPick(offer)
      }}
    />
  )
}

describe('the offer combobox', () => {
  it('searches on the server and shows what came back, unfiltered', async () => {
    const user = userEvent.setup()
    const calls = serveApi(() => ({ body: { offers: [FOUND] } }))
    const onPick = vi.fn()

    renderWithQuery(<Harness onPick={onPick} />)
    await user.click(screen.getByRole('combobox'))
    await user.type(screen.getByPlaceholderText(/search by id or name/i), '11104')

    // The server was asked, once the typing settled, with what was typed.
    await waitFor(() => {
      expect(calls.at(-1)?.search.get('q')).toBe('11104')
    })

    // And its answer is on screen. cmdk would have scored this row out of a list it filtered
    // itself — `11104` appears nowhere in `Nutrizen [BEAUTY-CL-BE_0155]` — so seeing it is
    // the proof that `shouldFilter={false}` is doing its job.
    const option = await screen.findByRole('option', { name: /Nutrizen/ })
    expect(option).toBeInTheDocument()

    await user.click(option)
    expect(onPick).toHaveBeenCalledWith(FOUND)
  })

  it('says when the catalogue has nothing, in the reference tool’s own words', async () => {
    const user = userEvent.setup()
    serveApi(() => ({ body: { offers: [] } }))

    renderWithQuery(<Harness onPick={vi.fn()} />)
    await user.click(screen.getByRole('combobox'))

    expect(await screen.findByText('No results found')).toBeInTheDocument()
  })
})
