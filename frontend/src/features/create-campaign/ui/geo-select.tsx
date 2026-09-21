import { cn } from 'cn'
import { ChevronsUpDownIcon } from 'lucide-react'
import type { Ref } from 'react'
import { useState } from 'react'

import type { Country } from '@/shared/lib/countries'
import { findCountry, searchCountries } from '@/shared/lib/countries'
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

type GeoSelectProps = {
  /** An ISO 3166-1 alpha-2 code, or `''` for nothing picked yet. */
  value: string
  onChange: (code: string) => void
  onBlur?: () => void
  disabled?: boolean
  invalid?: boolean
  id?: string
  /** The form library's, so that a server refusal naming this field can put the focus on it. */
  ref?: Ref<HTMLButtonElement>
}

/**
 * Pick the campaign's geo from the ISO 3166-1 list.
 *
 * A select and not a text input, and that is the whole point of the component: `XX` passes
 * `^[A-Z]{2}$`, and a campaign whose Flow 1 filters on XX is built, accepted, and never sees
 * a single click. The list is the one the API validates against — generated from it — so
 * anything this offers, the API takes.
 *
 * Same Popover-and-cmdk shape as the offer combobox next to it, with the one difference that
 * matters: the catalogue is searched on the server and this list is already here, so the
 * filtering is local and instant. `shouldFilter={false}` all the same — cmdk's own fuzzy
 * ranking would put Saint Vincent and the Grenadines above India for `in`.
 */
export function GeoSelect({
  value,
  onChange,
  onBlur,
  disabled = false,
  invalid = false,
  id,
  ref,
}: GeoSelectProps) {
  const [open, setOpen] = useState(false)
  const [term, setTerm] = useState('')

  const picked = value === '' ? null : findCountry(value)
  const found = searchCountries(term)

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) {
          setTerm('')
          // Closing the popover is when this field stops being touched, which is what a
          // form validating on blur waits for. The trigger's own blur fires on opening.
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
          className="w-full justify-between overflow-hidden font-normal"
        >
          {picked === null ? (
            <span className="text-muted-foreground">Select a country…</span>
          ) : (
            <CountryLabel country={picked} />
          )}
          <ChevronsUpDownIcon className="opacity-50" />
        </Button>
      </PopoverTrigger>

      <PopoverContent align="start" className="w-(--radix-popover-trigger-width) p-0">
        <Command shouldFilter={false} label="Countries">
          <CommandInput value={term} onValueChange={setTerm} placeholder="Search by code or name…" />
          <CommandList>
            {found.length === 0 ? <CommandEmpty>No results found</CommandEmpty> : null}
            {found.length > 0 ? (
              <CommandGroup>
                {found.map((country) => (
                  <CommandItem
                    key={country.code}
                    value={country.code}
                    data-checked={country.code === value}
                    onSelect={() => {
                      onChange(country.code)
                      setOpen(false)
                    }}
                  >
                    <CountryLabel country={country} />
                  </CommandItem>
                ))}
              </CommandGroup>
            ) : null}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

/** `MX  Mexico` — the code first, because the code is what gets typed and what gets sent. */
function CountryLabel({ country, className }: { country: Country; className?: string }) {
  return (
    <span className={cn('inline-flex items-baseline gap-2 truncate', className)}>
      <span className="text-muted-foreground w-6 shrink-0 font-medium tabular-nums">
        {country.code}
      </span>
      <span className="truncate">{country.name}</span>
    </span>
  )
}
