import { describe, expect, it } from 'vitest'

import { COUNTRIES, findCountry, searchCountries } from './countries'

describe('the list a campaign geo is picked from', () => {
  it('is the domain list, whole', () => {
    expect(COUNTRIES).toHaveLength(249)
    expect(findCountry('mx')?.name).toBe('Mexico')
    // The reason this is a select and not a text field: XX passes /^[A-Z]{2}$/ and would
    // build a campaign that never sees a click.
    expect(findCountry('XX')).toBeNull()
  })

  it('puts the code that was typed above the names that happen to contain it', () => {
    // `in` appears inside thirty country names. It is also India, which is what was meant.
    expect(searchCountries('in')[0]?.code).toBe('IN')
    expect(searchCountries('ind').map((country) => country.code)).toEqual(['IN', 'ID', 'IO'])
    // Same rank, so the shorter name wins: the United States before its Outlying Islands.
    expect(searchCountries('united').map((country) => country.code)).toEqual([
      'US',
      'GB',
      'AE',
      'UM',
    ])
  })

  it('finds a country through its diacritics', () => {
    expect(searchCountries('curacao')[0]?.code).toBe('CW')
    expect(searchCountries('cote')[0]?.code).toBe('CI')
    expect(searchCountries('aland')[0]?.code).toBe('AX')
  })
})
