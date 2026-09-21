import type { Offer } from '@/entities/offer'
import { findCountry } from '@/shared/lib/countries'

/**
 * The one geo mistake this form can catch before the campaign is built.
 *
 * Read the two flows in the order the tracker dispatches them. Flow 1 filters on the
 * campaign's country and redirects it to google.com; Flow 2 takes **everything else** and
 * rotates the offers. So the country named on this form is the one country that never
 * reaches the offer — and an offer set up for exactly that country is a campaign that
 * cannot work, built out of two settings that each look right on their own.
 *
 * That is the warning, and it is the opposite of the obvious one. "The offer's geo differs
 * from the campaign's" is the *normal* case here and would fire on nearly every campaign;
 * the overlap is the rare one, and it is the one worth a sentence.
 *
 * A warning and never a refusal: the offer's country list is the tracker's data, it is
 * advisory there too, and a buyer who means it can mean it.
 */
export function geoWarning(country: string, offer: Offer | null): string | null {
  if (offer === null || country === '') return null

  // `country` on an offer is Keitaro's own array and holds values that are not codes at
  // all — `-` is one of them. Anything the ISO list does not know is not an opinion.
  const geos = offer.country.flatMap((code) => findCountry(code)?.code ?? [])
  if (!geos.includes(country)) return null

  const rest = geos.filter((code) => code !== country)

  if (rest.length === 0) {
    return `${offer.name} настроен на ${country} — единственную страну, которую эта кампания сюда не пускает. Flow 1 ловит ${country} первым и уводит на google.com, так что оффер будет держать 100% ничего.`
  }

  return `${offer.name} закрывает ${geos.join(', ')}, а Flow 1 ловит ${country} первым и уводит на google.com. До оффера дойдут только ${rest.join(', ')}.`
}
