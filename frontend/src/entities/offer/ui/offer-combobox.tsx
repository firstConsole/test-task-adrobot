import { cn } from 'cn'
import { ChevronsUpDownIcon } from 'lucide-react'
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
                  <CommandEmpty>No results found</CommandEmpty>
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
