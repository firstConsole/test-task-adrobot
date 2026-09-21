import { cn } from 'cn'

import { StreamHeader, type Stream } from '@/entities/stream'
import { DraftActions } from '@/features/stream-draft'
import { TableBody, TableCell, TableHead, TableRow } from '@/shared/ui/table'

import { BAND_STYLE, groupStatus } from '../model/appearance'
import { COLUMN_COUNT } from '../model/columns'
import { AddOfferRow } from './add-offer-row'
import { OfferRow } from './offer-row'

/**
 * The only flow kind that rotates offers. `Flow 1` is a `redirect` and shows a heading and
 * nothing else — Keitaro ignores an offer list on it, so offering one would be a lie.
 */
const ROTATES = 'landings'

type StreamGroupProps = {
  campaignId: string
  stream: Stream
}

/**
 * One flow: its heading, then its rows, as a `<tbody>` of the one table on the screen.
 *
 * The heading is a `<th colSpan scope="colgroup">` rather than a styled `<td>` so that a
 * screen reader announces each offer together with the flow it belongs to. Two flows in one
 * table, told apart the way the markup means them to be.
 *
 * `PUSH TO KT` and `CANCEL` sit inside that heading cell, on a second line, and exist only
 * while the flow is dirty — which is also when the whole group turns amber. The unsaved thing
 * is the flow, so the flow is what is marked.
 */
export function StreamGroup({ campaignId, stream }: StreamGroupProps) {
  const streamId = stream.keitaro_stream_id
  const status = groupStatus(stream.dirty)

  return (
    <TableBody className="border-border border-t">
      <TableRow className={cn(BAND_STYLE[status])}>
        <TableHead
          colSpan={COLUMN_COUNT}
          scope="colgroup"
          className="h-auto py-2 align-top whitespace-normal"
        >
          <StreamHeader stream={stream} />
          {stream.dirty ? <DraftActions campaignId={campaignId} stream={stream} /> : null}
        </TableHead>
      </TableRow>

      {stream.schema === ROTATES ? (
        <>
          {stream.rows.map((row) => (
            <OfferRow
              key={row.offer_id}
              campaignId={campaignId}
              streamId={streamId}
              streamName={stream.name}
              row={row}
              status={status}
            />
          ))}
          <AddOfferRow
            campaignId={campaignId}
            streamId={streamId}
            streamName={stream.name}
            status={status}
          />
        </>
      ) : (
        <TableRow className={cn(BAND_STYLE[status])}>
          <TableCell colSpan={COLUMN_COUNT} className="text-muted-foreground whitespace-normal">
            A <code>{stream.schema}</code> flow sends every click to one place and rotates no
            offers.
          </TableCell>
        </TableRow>
      )}
    </TableBody>
  )
}
