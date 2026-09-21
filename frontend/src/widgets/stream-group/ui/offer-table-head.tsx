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
          Оффер
        </TableHead>
        <TableHead scope="col" className="w-24">
          Доля
        </TableHead>
        <TableHead scope="col" className="w-24">
          Клики
        </TableHead>
        <TableHead scope="col" className="w-40 text-right">
          Действия
        </TableHead>
      </TableRow>
    </TableHeader>
  )
}
