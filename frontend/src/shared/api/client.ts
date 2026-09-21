import createClient from 'openapi-fetch'

import type { components, paths } from './schema.gen'

/** The one body every failure of this API arrives in. */
export type Problem = components['schemas']['ProblemDetails']

/**
 * No `baseUrl`: the browser only ever calls `/api/…` on its own origin, and the proxy in
 * front — Vite in development, nginx in production — is what holds the shared token. No
 * VITE_ variable exists, so neither the token nor the tracker's address can be bundled.
 */
export const api = createClient<paths>()

export class ApiError extends Error {
  readonly problem: Problem

  constructor(problem: Problem) {
    super(problem.detail)
    this.name = 'ApiError'
    this.problem = problem
  }

  get status(): number {
    return this.problem.status
  }

  /** The stable slug to switch on — `type` carries the same fact as a URN. */
  get code(): string {
    return this.problem.code
  }

  /** On the response header and in every log line of the request that failed. */
  get correlationId(): string | null {
    return this.problem.correlation_id ?? null
  }
}

function isProblem(value: unknown): value is Problem {
  return (
    typeof value === 'object' &&
    value !== null &&
    'status' in value &&
    'detail' in value &&
    'code' in value
  )
}

/** What a proxy page, a dropped socket or an empty 500 becomes, so callers see one shape. */
function unreadable(response: Response): Problem {
  return {
    type: 'urn:adrobot:problem:unreadable-response',
    title: response.statusText || 'Request failed',
    status: response.status,
    detail: `The API answered ${String(response.status)} with a body this client could not read.`,
    code: 'unreadable_response',
  }
}

type Answer<T> = { data?: T; error?: unknown; response: Response }

/**
 * Turn openapi-fetch's `{ data, error }` into a value or a throw, because that is the shape
 * TanStack Query reads: a query that resolves is a query that succeeded.
 */
export async function unwrap<T>(pending: Promise<Answer<T>>): Promise<T> {
  const { data, error, response } = await pending
  if (error !== undefined) {
    throw new ApiError(isProblem(error) ? error : unreadable(response))
  }
  if (data === undefined) {
    throw new ApiError(unreadable(response))
  }
  return data
}
