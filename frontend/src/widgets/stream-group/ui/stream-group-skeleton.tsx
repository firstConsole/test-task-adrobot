import { Skeleton } from '@/shared/ui/skeleton'
import { TableBody, TableCell, TableHead, TableRow } from '@/shared/ui/table'

import { COLUMN_COUNT } from '../model/columns'

const ROWS = [0, 1, 2]

/**
 * One flow's worth of placeholder, in the shape the real group will have.
 *
 * The heights are the real ones — a heading band and rows two lines tall — because a skeleton
 * shorter than its content is a screen that jumps once the answer lands, which is worse than
 * no skeleton at all.
 */
export function StreamGroupSkeleton() {
  return (
    <TableBody className="border-border border-t">
      <TableRow className="hover:bg-transparent">
        <TableHead colSpan={COLUMN_COUNT} scope="colgroup" className="h-auto py-2">
          <Skeleton className="h-4 w-72" />
        </TableHead>
      </TableRow>

      {ROWS.map((row) => (
        <TableRow key={row} className="hover:bg-transparent">
          <TableCell className="py-3">
            <Skeleton className="h-4 w-[26rem]" />
            <Skeleton className="mt-1.5 h-4 w-32" />
          </TableCell>
          <TableCell>
            <Skeleton className="h-4 w-9" />
          </TableCell>
          <TableCell />
          <TableCell />
          <TableCell>
            <Skeleton className="ml-auto h-7 w-24" />
          </TableCell>
        </TableRow>
      ))}
    </TableBody>
  )
}
