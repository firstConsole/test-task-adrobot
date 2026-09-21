import { ApiError } from '@/shared/api/client'
import { problemMessage } from '@/shared/lib/problem-message'

/** The two sources whose fields are a form's fields. `query.…` is neither. */
const FROM_US = 'body'
const FROM_TRACKER = 'tracker'

export type Refusals<Field extends string> = {
  /** One message per field the API named, in the order it named them. */
  fields: { field: Field; message: string }[]
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
 * A location the caller has no field for is not swallowed. It goes into `message` with its
 * location intact, which is the difference between a form that refuses to submit and a form
 * that refuses to submit for a reason nobody can find.
 */
export function readRefusals<Field extends string>(
  error: unknown,
  fields: ReadonlySet<Field>,
): Refusals<Field> {
  const problem = error instanceof ApiError ? error.problem : null

  if (problem === null) {
    return {
      fields: [],
      message: problemMessage(error, 'The API could not be reached.'),
      correlationId: null,
    }
  }

  const placed: { field: Field; message: string }[] = []
  const unplaced: string[] = []

  for (const invalid of problem.errors ?? []) {
    const [source, ...path] = invalid.location.split('.')
    const name = path.join('.')

    // The cast is what the `has` on the line above just established: the set holds the
    // caller's field names, and this is one of them.
    if ((source === FROM_US || source === FROM_TRACKER) && (fields as ReadonlySet<string>).has(name)) {
      placed.push({
        field: name as Field,
        message: source === FROM_TRACKER ? `Keitaro: ${invalid.message}` : invalid.message,
      })
    } else {
      unplaced.push(`${invalid.location}: ${invalid.message}`)
    }
  }

  return {
    fields: placed,
    // The API writes `detail` for a buyer to read, so it is the line to show when nothing
    // else placed itself. Once every complaint sits under its own input, saying it again
    // above the button is just noise.
    message: unplaced.length > 0 ? unplaced.join(' · ') : placed.length > 0 ? null : problem.detail,
    correlationId: problem.correlation_id ?? null,
  }
}
