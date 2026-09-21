import { TableHead, TableHeader, TableRow } from '@/shared/ui/table'

/**
 * The one header of the whole screen: the table has a `<thead>` and then a `<tbody>` per
 * flow, so the columns are declared once and every flow lines up under them.
 *
 * The reference tool leaves the first header blank. We name it, because a blank `<th>` is
 * what a screen reader reads out for every offer cell below it.
 */
export function OfferTableHead() {
  return (
    <TableHeader>
      <TableRow className="hover:bg-transparent">
        <TableHead scope="col" className="w-1/2">
          Offer
        </TableHead>
        <TableHead scope="col" className="w-24">
          Share
        </TableHead>
        <TableHead scope="col" className="w-24">
          Stats
        </TableHead>
        <TableHead scope="col" className="w-32">
          Trends
        </TableHead>
        <TableHead scope="col" className="w-40 text-right">
          Actions
        </TableHead>
      </TableRow>
    </TableHeader>
  )
}
