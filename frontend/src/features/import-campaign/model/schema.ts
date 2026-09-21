import { z } from 'zod'

/**
 * The one number that opens a campaign somebody else built.
 *
 * Held as the text that was typed and turned into a number by the schema, rather than by an
 * `<input type="number">`: a number input hands back `NaN` for an empty field and for `12e4`
 * alike, and "expected number, received NaN" is not a sentence to show anybody. The digits
 * are checked as digits, and what comes out the other side is an integer.
 */
export const importCampaignSchema = z.object({
  keitaro_campaign_id: z
    .string()
    .trim()
    .regex(/^\d+$/, 'Id кампании — это число из её адреса в трекере, например 93212.')
    .transform(Number)
    .refine((id) => id > 0, 'Кампании 0 не существует.'),
})

export type ImportCampaignValues = z.output<typeof importCampaignSchema>

/** The names a server refusal can land on. One field, one name, and it is the body's. */
export const FORM_FIELDS: ReadonlySet<keyof ImportCampaignValues> = new Set(['keitaro_campaign_id'])
