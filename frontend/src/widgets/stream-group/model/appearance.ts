/**
 * How a flow's rows are painted, as literal strings looked up by key.
 *
 * Tailwind 4 removed `safelist`, so no class name on this screen may be assembled at
 * runtime — `bg-${tone}-50` compiles to nothing and fails silently. Every state is written
 * out here instead.
 *
 * **Dirty is a property of the whole group, not of a row.** The reference tool fills the
 * heading, every row, the struck-out rows and the add footer in one colour, because what is
 * unsaved is the flow, and a single highlighted row would say the opposite.
 *
 * Colour is never the only channel: a dirty group is a fill *and* a left stripe *and* two
 * buttons that were not there a moment ago.
 */

/** An offer row, which highlights under the pointer like the rest of the table. */
export const ROW_STYLE = {
  clean: '',
  dirty: 'border-l-2 border-l-amber-400 bg-amber-50 hover:bg-amber-100/60',
} as const

/** The heading and the add footer: structural bands, so they do not react to the pointer. */
export const BAND_STYLE = {
  clean: 'hover:bg-transparent',
  dirty: 'border-l-2 border-l-amber-400 bg-amber-50 hover:bg-amber-50',
} as const

/**
 * A row taken out of the division. It keeps its place, its label and its `(preview)` link —
 * the reference tool leaves it on screen, and a removal that survives a push is the most
 * characteristic thing this editor does. Grey is not the only channel: the label says
 * `(removed)` in words and the action button says `BRING BACK`.
 */
export const REMOVED_ROW = 'text-muted-foreground'

export type GroupStatus = keyof typeof ROW_STYLE

export function groupStatus(dirty: boolean): GroupStatus {
  return dirty ? 'dirty' : 'clean'
}
