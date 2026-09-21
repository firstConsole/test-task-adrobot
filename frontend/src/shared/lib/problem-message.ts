import { ApiError } from '@/shared/api/client'

/**
 * What to put in front of a person when a call fails.
 *
 * The API writes its `detail` for a buyer to read — "Flow 2 would be left with no active
 * offers" — so that message is the one to show. Anything that is not a problem document is
 * a dropped socket or a proxy page, and gets the caller's own words instead.
 */
export function problemMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError && error.message !== '' ? error.message : fallback
}
