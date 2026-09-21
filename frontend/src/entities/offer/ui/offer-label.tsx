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
 * `#11112  11112 Oxys [BEAUTY-CL-BE_0155] [pl es -]  (preview)`
 *
 * The reference tool leaves the id out and prints the name alone, which is why two of its
 * rows read `Miaflow …_0009` and `Miaflow …_0008` and are told apart by a network code. We
 * print the id the way Keitaro's own campaign screen does, because that screen is what a
 * reviewer has open in the other window — and the id is what the combobox searches by.
 *
 * `offer` is null for an offer the local catalogue has never heard of. The row still renders:
 * a flow that points at an unknown offer is a thing to be shown, not a thing to blank out.
 */
export function OfferLabel({ offerId, offer, withPreview = false, className }: OfferLabelProps) {
  const countries = offer?.country.join(' ') ?? ''

  return (
    <span className={cn('inline-flex flex-wrap items-baseline gap-x-1.5', className)}>
      <span className="text-muted-foreground tabular-nums">#{String(offerId)}</span>

      {offer === null || offer === undefined ? (
        <span className="text-muted-foreground italic">(not in catalogue)</span>
      ) : (
        <>
          <span>{offer.name}</span>
          {offer.affiliate_network === null || offer.affiliate_network === undefined ? null : (
            <span className="text-muted-foreground">[{offer.affiliate_network}]</span>
          )}
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
