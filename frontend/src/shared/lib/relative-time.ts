/**
 * `3 минуты назад` — сколько прошло с момента, о котором сказал API.
 *
 * Pinned to `ru` rather than the browser's locale: the screen is written in Russian, and a
 * mirror line reading `3 minutes ago` under a Russian sentence would be the one string that
 * followed the visitor's machine instead of the page. The command buttons stay English
 * because they are quotations from the reference tool, not prose.
 */
const FORMAT = new Intl.RelativeTimeFormat('ru', { numeric: 'auto' })

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
