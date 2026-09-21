import { describe, expect, it } from 'vitest'

import type { Offer } from '@/entities/offer'

import { geoWarning } from './geo-warning'

function anOffer(country: string[]): Offer {
  return { id: 11112, name: 'Oxys', state: 'active', country }
}

describe('the geo the offer will never be shown', () => {
  it('says nothing about the ordinary case', () => {
    // Flow 2 takes everything Flow 1 did not, so an offer for other countries is the
    // arrangement working. Warning here would fire on nearly every campaign.
    expect(geoWarning('MX', anOffer(['PL', 'ES']))).toBeNull()
    expect(geoWarning('MX', anOffer([]))).toBeNull()
    expect(geoWarning('MX', null)).toBeNull()
    expect(geoWarning('', anOffer(['MX']))).toBeNull()
  })

  it('warns when the offer is set up for exactly the country Flow 1 takes away', () => {
    expect(geoWarning('MX', anOffer(['mx']))).toContain('100% ничего')
  })

  it('names what is left when the offer covers more than that one country', () => {
    expect(geoWarning('MX', anOffer(['MX', 'PL', 'ES']))).toBe(
      'Oxys закрывает MX, PL, ES, а Flow 1 ловит MX первым и уводит на google.com. До оффера дойдут только PL, ES.',
    )
  })

  it('ignores what the tracker keeps in that array that is not a country', () => {
    expect(geoWarning('MX', anOffer(['-']))).toBeNull()
    expect(geoWarning('MX', anOffer(['MX', '-']))).toContain('100% ничего')
  })
})
