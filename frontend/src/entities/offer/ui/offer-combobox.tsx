import { cn } from 'cn'
import { ChevronsUpDownIcon } from 'lucide-react'
import type { ReactNode, Ref } from 'react'
import { useState } from 'react'

import { ApiError } from '@/shared/api/client'
import { useDebouncedValue } from '@/shared/lib/use-debounced-value'
import { Button } from '@/shared/ui/button'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/shared/ui/command'
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/ui/popover'

import { useOfferSearch } from '../api/queries'
import type { Offer } from '../model/types'
import { OfferLabel } from './offer-label'

const DEBOUNCE_MS = 250

type OfferComboboxProps = {
  value: Offer | null
  onChange: (offer: Offer) => void
  onBlur?: () => void
  disabled?: boolean
  invalid?: boolean
  placeholder?: string
  id?: string
  /** The form library's, so that a server refusal naming this field can put the focus on it. */
  ref?: Ref<HTMLButtonElement>
  /**
   * What to offer when the search comes back with nothing — in practice the button that
   * reads the catalogue from Keitaro.
   *
   * A slot and not the button itself: refreshing the catalogue is a mutation, mutations live
   * in `features/`, and this component is an entity. Whoever mounts it is on a layer that may
   * reach both.
   */
  empty?: ReactNode
  className?: string
}

/**
 * Pick one offer out of the catalogue, searching on the server.
 *
 * **`shouldFilter={false}` is the line that makes this work.** Left on, cmdk filters the
 * server's answer a second time by its own fuzzy algorithm: type `11104` and the offer named
 * `11104 Nutrizen` is scored out, the list goes quiet, and nothing anywhere reports an error.
 *
 * Controlled, with no `ADD` of its own: the reference tool puts the button beside the select
 * and the create-campaign form binds the same component to react-hook-form.
 */
export function OfferCombobox({
  value,
  onChange,
  onBlur,
  disabled = false,
  invalid = false,
  placeholder = 'Select an offer…',
  id,
  ref,
  empty,
  className,
}: OfferComboboxProps) {
  const [open, setOpen] = useState(false)
  const [term, setTerm] = useState('')
  const settled = useDebouncedValue(term, DEBOUNCE_MS)
  const search = useOfferSearch(settled, { enabled: open })

  const offers = search.data?.offers ?? []
  // The debounce window is a third state the query itself cannot report: the key has not
  // changed yet, so nothing is pending and nothing is placeholder — and the list on screen
  // is already out of date.
  const searching = term !== settled || search.isPending || search.isPlaceholderData

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) {
          setTerm('')
          // Closing is when this field stops being touched, which is what a form validating
          // on blur waits for — the trigger's own blur fires on the way in, not on the way out.
          onBlur?.()
        }
      }}
    >
      <PopoverTrigger asChild>
        <Button
          id={id}
          ref={ref}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-invalid={invalid || undefined}
          disabled={disabled}
          className={cn('w-full justify-between overflow-hidden font-normal', className)}
        >
          {value === null ? (
            <span className="text-muted-foreground">{placeholder}</span>
          ) : (
            <OfferLabel offerId={value.id} offer={value} className="flex-nowrap" />
          )}
          <ChevronsUpDownIcon className="opacity-50" />
        </Button>
      </PopoverTrigger>

      <PopoverContent align="start" className="w-(--radix-popover-trigger-width) p-0">
        <Command shouldFilter={false} label="Offer catalogue">
          <CommandInput value={term} onValueChange={setTerm} placeholder="Search by id or name…" />
          <CommandList>
            {search.isError ? (
              <p role="status" className="text-destructive px-3 py-6 text-center text-sm">
                {search.error instanceof ApiError ? search.error.message : 'Search failed'}
              </p>
            ) : (
              <>
                {searching ? (
                  <p role="status" className="text-muted-foreground py-6 text-center text-sm">
                    Searching…
                  </p>
                ) : null}

                {!searching && offers.length === 0 ? (
                  <CommandEmpty>
                    {/* Two different facts, and telling them apart is the whole point: with
                        no term typed this endpoint answers SHOW ALL OFFERS, so nothing back
                        means the local catalogue is empty — not that the tracker has no such
                        offer. One of them is fixed by pressing a button. */}
                    <span className="block">
                      {settled === ''
                        ? 'The catalogue is empty — nothing has been read from Keitaro yet'
                        : 'No results found'}
                    </span>
                    {empty === undefined ? null : <span className="mt-3 block">{empty}</span>}
                  </CommandEmpty>
                ) : null}

                {offers.length > 0 ? (
                  <CommandGroup className={searching ? 'opacity-50' : undefined}>
                    {offers.map((offer) => (
                      <CommandItem
                        key={offer.id}
                        value={String(offer.id)}
                        data-checked={value?.id === offer.id}
                        onSelect={() => {
                          onChange(offer)
                          setOpen(false)
                        }}
                      >
                        <OfferLabel offerId={offer.id} offer={offer} />
                      </CommandItem>
                    ))}
                  </CommandGroup>
                ) : null}
              </>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
