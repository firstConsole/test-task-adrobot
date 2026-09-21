/**
 * The four columns of the offer table.
 *
 * The reference tool has a fifth, `Trends`, and it is empty in every frame of the video —
 * there is no per-day report behind this API to fill it with, and a column that will never
 * fill is worse than four honest ones. It is also the first thing the plan cuts.
 *
 * Exported as a count because the flow heading spans all of them: a `colSpan` that drifts out
 * of step with the header is a row that quietly stops lining up.
 */
export const COLUMN_COUNT = 4
