/**
 * `3 minutes ago` — how long since an instant the API reported.
 *
 * Pinned to `en` rather than the browser's locale: every other word on this screen is
 * English, and a mirror line that read «3 минуты назад» under `FETCH STREAMS FROM KT` would
 * be the one localised string in the app.
 */
const FORMAT = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

/** Each unit and how many of it make the next one; the last span is a stop, not a divisor. */
const STEPS: readonly [Intl.RelativeTimeFormatUnit, number][] = [
  ['second', 60],
  ['minute', 60],
  ['hour', 24],
  ['day', 7],
  ['week', 4.35],
  ['month', 12],
  ['year', Number.POSITIVE_INFINITY],
]

export function relativeTime(iso: string, now: number = Date.now()): string {
  let amount = (new Date(iso).getTime() - now) / 1000

  for (const [unit, span] of STEPS) {
    if (Math.abs(amount) < span) return FORMAT.format(Math.round(amount), unit)
    amount /= span
  }

  return FORMAT.format(Math.round(amount), 'year')
}
