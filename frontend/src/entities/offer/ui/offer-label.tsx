import { cn } from 'cn'

import type { Offer } from '../model/types'

type OfferLabelProps = {
  /** From the flow row, which always has one even when the catalogue does not. */
  offerId: number
  offer?: Offer | null
  /** The `(preview)` link the table row carries; the dropdown item does not want an anchor. */
  withPreview?: boolean
  className?: string
}

/**
 * One offer, spelled the same way in the table and in the dropdown:
 *
 * `#3749  Miaflow [BEAUTY-RO-BE_0009] [pl - - spin ro -] 2. Основной, Румыния JS  [Romania]`
 *
 * The bracket soup is the offer's **name**, exactly as the tracker stores it — the reference
 * tool printed that name and nothing else, which is why two of its rows read `Miaflow …_0009`
 * and `Miaflow …_0008` and are told apart by a code buried mid-string. We put the id in
 * front the way Keitaro's own campaign screen does, because that screen is what a reviewer
 * has open in the other window, and because the id is what the combobox searches by.
 *
 * `affiliate_network` is deliberately not drawn. Keitaro documents it as the network's name,
 * and the tracker we wrap answers `"62"` for every offer — an id wearing a name's type. A
 * `[62]` beside a row is noise, and the readable network code is already inside the name.
 *
 * `offer` is null for an offer the local catalogue has never heard of. The row still renders:
 * a flow that points at an unknown offer is a thing to be shown, not a thing to blank out.
 */
export function OfferLabel({ offerId, offer, withPreview = false, className }: OfferLabelProps) {
  const countries = offer?.country.join(', ') ?? ''

  return (
    <span className={cn('inline-flex flex-wrap items-baseline gap-x-1.5', className)}>
      <span className="text-muted-foreground tabular-nums">#{String(offerId)}</span>

      {offer === null || offer === undefined ? (
        <span className="text-muted-foreground italic">(not in catalogue)</span>
      ) : (
        <>
          <span>{offer.name}</span>
          {countries === '' ? null : <span className="text-muted-foreground">[{countries}]</span>}
          {offer.state === 'active' ? null : (
            <span className="text-destructive">({offer.state})</span>
          )}
          {withPreview && offer.preview_url !== null && offer.preview_url !== undefined ? (
            <a
              href={offer.preview_url}
              target="_blank"
              rel="noreferrer"
              className="text-primary underline underline-offset-2"
              onClick={(event) => {
                event.stopPropagation()
              }}
            >
              (preview)
            </a>
          ) : null}
        </>
      )}
    </span>
  )
}
