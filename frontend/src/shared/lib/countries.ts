import type { Country } from './countries.gen'
import { COUNTRIES } from './countries.gen'

export type { Country }
export { COUNTRIES }

const BY_CODE = new Map(COUNTRIES.map((country) => [country.code, country]))

/**
 * The country a code names, or null.
 *
 * Null is a real answer and not a failure: an offer's country array is the tracker's data,
 * and Keitaro keeps `-` and other non-codes in it.
 */
export function findCountry(code: string): Country | null {
  return BY_CODE.get(code.trim().toUpperCase()) ?? null
}

/** Fold to what a search compares: no case, no diacritics — `curacao` has to find Curaçao. */
function fold(value: string): string {
  return value
    .normalize('NFD')
    .replace(/\p{M}/gu, '')
    .toLowerCase()
}

const FOLDED = COUNTRIES.map((country) => ({ country, name: fold(country.name) }))

/** Where a country matched, lowest first. Only the order matters, not the numbers. */
const NO_MATCH = -1

/**
 * The countries a query narrows to, best match first.
 *
 * Ranked and not merely filtered, because the two-letter codes are what a buyer types and
 * every one of them also appears inside some country's name: `in` is India, and a list that
 * opens on Saint Vincent and the Grenadines makes you scroll past thirty rows to the answer
 * you already spelled out.
 */
export function searchCountries(term: string): readonly Country[] {
  const query = fold(term.trim())
  if (query === '') return COUNTRIES

  const matches: { country: Country; rank: number }[] = []

  for (const { country, name } of FOLDED) {
    const code = country.code.toLowerCase()
    const rank =
      code === query
        ? 0
        : code.startsWith(query)
          ? 1
          : name.startsWith(query)
            ? 2
            : name.includes(query)
              ? 3
              : NO_MATCH

    if (rank !== NO_MATCH) matches.push({ country, rank })
  }

  // Then by name length, which is a cheap stand-in for "more specific": `ind` should offer
  // India before Indonesia, and `united` the United States before its Minor Outlying
  // Islands. Sorting is specified stable, so an exact tie keeps the order the list was
  // built in — by code, the way the domain keeps it.
  matches.sort(
    (left, right) =>
      left.rank - right.rank || left.country.name.length - right.country.name.length,
  )

  return matches.map((match) => match.country)
}
