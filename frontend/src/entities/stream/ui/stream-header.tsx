import { Badge } from '@/shared/ui/badge'

import type { Stream, StreamFilter } from '../model/types'

/**
 * One flow's conditions in the tracker's own words — `country accept "AU"`.
 *
 * The reference tool prints `None` here for every flow, including the one that really does
 * filter by country; we print what the flow carries and keep its word for carrying none, so
 * that a heading a reviewer recognises is also a heading that is true.
 */
function describeFilters(filters: readonly StreamFilter[]): string {
  if (filters.length === 0) return 'None'

  return filters
    .map((filter) => {
      const values = filter.payload.map((value) => `"${value}"`).join(', ')
      return values === '' ? `${filter.name} ${filter.mode}` : `${filter.name} ${filter.mode} ${values}`
    })
    .join('; ')
}

type StreamHeaderProps = {
  stream: Stream
  /** From the statistics query, which is a separate request: `null` until it answers. */
  clicksToday?: number | null
}

/**
 * The line a flow's group of rows is introduced by:
 *
 * `Stream: Flow 2 (#2)  filter by country accept "AU", stream_id 564221, clicks today: 1`
 *
 * Text only, with no table markup of its own: `widgets/stream-group` is what puts it into a
 * `<th colSpan scope="colgroup">`, so that a screen reader announces each row together with
 * the flow it belongs to.
 */
export function StreamHeader({ stream, clicksToday }: StreamHeaderProps) {
  const position = stream.position ?? null

  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
      <span className="text-sm font-semibold">
        Stream: {stream.name}
        {position === null ? null : ` (#${String(position)})`}
      </span>
      <span className="text-muted-foreground text-xs font-normal">
        filter by {describeFilters(stream.filters)}, stream_id {String(stream.keitaro_stream_id)}
        {clicksToday === null || clicksToday === undefined
          ? null
          : `, clicks today: ${String(clicksToday)}`}
      </span>
      {stream.absent ? (
        <Badge variant="destructive" title="Keitaro no longer lists this flow">
          missing in Keitaro
        </Badge>
      ) : null}
    </div>
  )
}
