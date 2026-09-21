import { cn } from 'cn'

import type { Stream } from '@/entities/stream'

/** The three kinds of change, each with a mark, a colour and a word — never colour alone. */
const CHANGE = {
  added: { mark: '+', label: 'added', tone: 'text-emerald-700' },
  removed: { mark: '−', label: 'removed', tone: 'text-red-700' },
  brought_back: { mark: '↺', label: 'brought back', tone: 'text-amber-800' },
} as const

type ChangeKind = keyof typeof CHANGE

function ChangedOffer({ offerId, name, kind }: { offerId: number; name: string | null; kind: ChangeKind }) {
  const { mark, label, tone } = CHANGE[kind]

  return (
    <span className={cn('inline-flex max-w-56 items-baseline gap-1', tone)} title={name ?? undefined}>
      <span aria-hidden>{mark}</span>
      <span className="sr-only">{label}:</span>
      <span className="tabular-nums">#{String(offerId)}</span>
      {name === null ? null : <span className="min-w-0 truncate">{name}</span>}
    </span>
  )
}

/**
 * What pressing `PUSH TO KT` would do, said before it is pressed.
 *
 * The reference tool has nothing like this, and a media buyer about to write to a live
 * tracker wants exactly it. Every number here is read off the server's own `diff` — `desired`
 * is not a description of the payload but the payload itself, rendered by the same function
 * the push sends — so the line below is a rehearsal rather than a second opinion.
 */
export function DiffSummary({ stream }: { stream: Stream }) {
  const diff = stream.diff
  if (diff === null || diff === undefined) return null

  const nameOf = (offerId: number) =>
    stream.rows.find((row) => row.offer_id === offerId)?.offer?.name ?? null

  // The numbers come from `desired`, which is the payload itself; the order comes from the
  // rows, which is the order the table above is in. Read straight off `desired` this would
  // say 33/33/34 over a table reading 34/33/33 — the same multiset, and a reviewer left
  // wondering which of the two to believe.
  const willWrite = new Map(diff.desired.map((row) => [row.offer_id, row]))
  const shares = stream.rows
    .flatMap((row) => {
      const target = willWrite.get(row.offer_id)
      return target !== undefined && target.state === 'active' ? [String(target.share)] : []
    })
    .join('/')
  const disabled = diff.desired.filter((row) => row.state !== 'active').length

  const changed: { kind: ChangeKind; offerId: number }[] = [
    ...diff.added.map((offerId) => ({ kind: 'added' as const, offerId })),
    ...diff.brought_back.map((offerId) => ({ kind: 'brought_back' as const, offerId })),
    ...diff.removed.map((offerId) => ({ kind: 'removed' as const, offerId })),
  ]

  return (
    <div className="mt-2 space-y-1 text-xs font-normal">
      <p className="text-amber-900">
        Push will write <span className="font-semibold tabular-nums">{shares}</span> to Keitaro
        {disabled === 0 ? null : `, and ${String(disabled)} row${disabled === 1 ? '' : 's'} as disabled`}.
      </p>

      {changed.length === 0 && diff.share_changes.length === 0 ? null : (
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          {changed.map(({ kind, offerId }) => (
            <ChangedOffer key={`${kind}-${String(offerId)}`} offerId={offerId} name={nameOf(offerId)} kind={kind} />
          ))}

          {diff.share_changes.map((change) => (
            <span key={change.offer_id} className="text-muted-foreground tabular-nums">
              #{String(change.offer_id)} {String(change.was)}
              <span aria-hidden> → </span>
              <span className="sr-only"> becomes </span>
              {String(change.now)}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
