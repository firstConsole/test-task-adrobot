import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { campaignNumbers } from '@/entities/stats'
import { serveApi } from '@/shared/test/api-double'
import { aRow, aStream } from '@/shared/test/factories'
import { inTable, renderWithQuery } from '@/shared/test/render'

import { StreamGroup } from './stream-group'

const CAMPAIGN = '4a34da83-ddca-4190-851c-dc64dcf4cc7b'

const ROWS = [
  aRow({ offer_id: 3749, share: 50, offer: { id: 3749, name: 'Miaflow 0009', state: 'active', country: [] } }),
  aRow({ offer_id: 3717, share: 50, offer: { id: 3717, name: 'Miaflow 0008', state: 'active', country: [] } }),
  aRow({ offer_id: 11111, share: 0, removed: true, offer: { id: 11111, name: 'FitoMishki', state: 'active', country: [] } }),
]

const NO_NUMBERS = campaignNumbers({ day: '2026-09-21', timezone: 'UTC', available: true, stale: false, streams: [], offers: [] })

describe('a flow with staged edits', () => {
  it('offers PUSH TO KT and CANCEL, which a clean flow does not', () => {
    serveApi(() => ({ body: {} }))
    const dirty = aStream({ rows: ROWS, dirty: true, can_push: true })

    const { rerender } = renderWithQuery(
      inTable(<StreamGroup campaignId={CAMPAIGN} stream={dirty} numbers={NO_NUMBERS} />),
    )
    expect(screen.getByRole('button', { name: /push flow 2 to keitaro/i })).toBeEnabled()
    expect(screen.getByRole('button', { name: /throw away the staged edits/i })).toBeInTheDocument()

    rerender(
      inTable(
        <StreamGroup
          campaignId={CAMPAIGN}
          stream={aStream({ rows: ROWS, dirty: false })}
          numbers={NO_NUMBERS}
        />,
      ),
    )
    expect(screen.queryByRole('button', { name: /push flow 2 to keitaro/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /throw away the staged edits/i })).not.toBeInTheDocument()
  })

  it('will not let a blocked push be pressed, and says why in the server’s own words', () => {
    serveApi(() => ({ body: {} }))
    const blocked = aStream({
      rows: ROWS,
      dirty: true,
      can_push: false,
      block_reason: 'Flow 2 would have no active offer left — its traffic would go nowhere',
    })

    renderWithQuery(inTable(<StreamGroup campaignId={CAMPAIGN} stream={blocked} numbers={NO_NUMBERS} />))

    expect(screen.getByRole('button', { name: /push flow 2 to keitaro/i })).toBeDisabled()
    expect(screen.getByText(/traffic would go nowhere/i)).toBeInTheDocument()
  })
})

describe('a removed row', () => {
  it('keeps its place, says so in words, shows 0% and offers BRING BACK', () => {
    serveApi(() => ({ body: {} }))

    renderWithQuery(
      inTable(
        <StreamGroup
          campaignId={CAMPAIGN}
          stream={aStream({ rows: ROWS, dirty: true, can_push: true })}
          numbers={NO_NUMBERS}
        />,
      ),
    )

    const row = screen.getByRole('row', { name: /FitoMishki in Flow 2, removed/i })
    expect(within(row).getByText('(removed)')).toBeInTheDocument()
    expect(within(row).getByText('0%')).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: /bring FitoMishki back/i })).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /^remove /i })).not.toBeInTheDocument()

    // And the rows that are still in the division keep theirs.
    const kept = screen.getByRole('row', { name: /Miaflow 0009 in Flow 2$/i })
    expect(within(kept).getByRole('button', { name: /remove Miaflow 0009/i })).toBeInTheDocument()
  })
})

describe('a redirect flow', () => {
  it('draws its heading and nothing to rotate offers with', () => {
    serveApi(() => ({ body: {} }))

    renderWithQuery(
      inTable(
        <StreamGroup
          campaignId={CAMPAIGN}
          stream={aStream({ keitaro_stream_id: 564220, name: 'Flow 1', schema: 'redirect', position: 1 })}
          numbers={NO_NUMBERS}
        />,
      ),
    )

    expect(screen.getByText(/rotates no offers/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /add an offer/i })).not.toBeInTheDocument()
  })
})

describe('the Stats column', () => {
  it('issues no request of its own, however many rows there are', () => {
    const calls = serveApi(() => ({ body: {} }))
    const numbers = campaignNumbers({
      day: '2026-09-21',
      timezone: 'UTC',
      available: true,
      stale: false,
      streams: [{ keitaro_stream_id: 564221, clicks: 7 }],
      offers: [{ offer_id: 3749, clicks: 4, conversions: 1 }],
    })

    renderWithQuery(
      inTable(<StreamGroup campaignId={CAMPAIGN} stream={aStream({ rows: ROWS })} numbers={numbers} />),
    )

    // The numbers are on screen, read out of the map the page built once.
    expect(screen.getByText('4')).toBeInTheDocument()
    expect(screen.getByText(/1 cv/)).toBeInTheDocument()
    expect(screen.getByText(/clicks today: 7/)).toBeInTheDocument()
    // An offer the report did not mention gets an empty cell, never an invented zero.
    expect(screen.queryByText('0', { selector: 'span' })).not.toBeInTheDocument()
    expect(calls).toHaveLength(0)
  })
})
