import { ApiError } from '@/shared/api/client'
import { problemMessage } from '@/shared/lib/problem-message'

import type { CreateCampaignValues } from './schema'

type FormField = keyof CreateCampaignValues

const FORM_FIELDS = new Set<string>(['name', 'country', 'offer_id'] satisfies FormField[])

/** The two sources whose fields are this form's fields. `query.…` is neither. */
const FROM_US = 'body'
const FROM_TRACKER = 'tracker'

export type Refusals = {
  /** One message per field the API named, in the order it named them. */
  fields: { field: FormField; message: string }[]
  /** What is left to say once the fields have said their part, or null if nothing is. */
  message: string | null
  correlationId: string | null
}

/**
 * Sort a refused request into the fields it is about and the part that has to be said aloud.
 *
 * The API dots its locations from the outside in — `body.country`, `query.limit` — and
 * prefixes `tracker.` to a complaint Keitaro made about a field we passed on. Both kinds
 * belong under the same input, so both are matched here; only the tracker's are labelled,
 * because "this offer is not in the tracker" and "we do not like this offer" are different
 * statements and a buyer should be able to tell which one they are reading.
 *
 * A location this form has no field for is not swallowed. It goes into `message` with its
 * location intact, which is the difference between a form that refuses to submit and a form
 * that refuses to submit for a reason nobody can find.
 */
export function readRefusals(error: unknown): Refusals {
  const problem = error instanceof ApiError ? error.problem : null

  if (problem === null) {
    return {
      fields: [],
      message: problemMessage(error, 'The API could not be reached.'),
      correlationId: null,
    }
  }

  const fields: { field: FormField; message: string }[] = []
  const unplaced: string[] = []

  for (const invalid of problem.errors ?? []) {
    const [source, ...path] = invalid.location.split('.')
    const field = path.join('.')

    if ((source === FROM_US || source === FROM_TRACKER) && FORM_FIELDS.has(field)) {
      fields.push({
        field: field as FormField,
        message: source === FROM_TRACKER ? `Keitaro: ${invalid.message}` : invalid.message,
      })
    } else {
      unplaced.push(`${invalid.location}: ${invalid.message}`)
    }
  }

  return {
    fields,
    // The API writes `detail` for a buyer to read, so it is the line to show when nothing
    // else placed itself. Once every complaint sits under its own input, saying it again
    // above the button is just noise.
    message: unplaced.length > 0 ? unplaced.join(' · ') : fields.length > 0 ? null : problem.detail,
    correlationId: problem.correlation_id ?? null,
  }
}
