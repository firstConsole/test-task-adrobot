import { z } from 'zod'

import { findCountry } from '@/shared/lib/countries'

/**
 * Mirrors `MAX_CAMPAIGN_NAME` in the backend's `domain/values.py`.
 *
 * A mirror and not a source: the same bound is checked again on the server, and a name this
 * one lets through comes back as a 422 that lands on this field. The schema exists to
 * answer before the round trip, not instead of it.
 */
const MAX_NAME = 255

/**
 * Part 1's three fields, in the shape the request body has.
 *
 * The form's values *are* the body — same names, same types, snake_case and all — so
 * submitting is `mutate(values)` with no mapping in between to fall out of step, and a 422
 * naming `body.country` is a field on this form without a translation table.
 *
 * `''` and `0` are the empty states, and they are safe ones: neither is a value the API
 * accepts, so a field nobody touched cannot be mistaken for a field somebody filled in.
 */
export const createCampaignSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, 'Give the campaign a name — it is how you will find it in the tracker.')
    .max(MAX_NAME, `Keitaro takes at most ${String(MAX_NAME)} characters.`),
  country: z
    .string()
    .refine((code) => findCountry(code) !== null, 'Pick the country Flow 1 catches.'),
  offer_id: z.number().int().positive('Pick the offer Flow 2 rotates.'),
})

export type CreateCampaignValues = z.infer<typeof createCampaignSchema>

export const EMPTY_CAMPAIGN: CreateCampaignValues = { name: '', country: '', offer_id: 0 }
