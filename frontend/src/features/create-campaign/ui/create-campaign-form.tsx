import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { Controller, useForm } from 'react-hook-form'

import type { Offer } from '@/entities/offer'
import { OfferCombobox } from '@/entities/offer'
import { Button } from '@/shared/ui/button'
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldSet,
} from '@/shared/ui/field'
import { Input } from '@/shared/ui/input'

import { useCreateCampaign } from '../api/use-create-campaign'
import type { CreateCampaignValues } from '../model/schema'
import { EMPTY_CAMPAIGN, createCampaignSchema } from '../model/schema'
import { GeoSelect } from './geo-select'

/**
 * Part 1's form: a name, a geo and an offer, and Keitaro has a campaign.
 *
 * Validated twice on purpose. This schema answers the obvious refusals without a round trip;
 * the API refuses again, over rules only it can hold — an offer that is not in the tracker,
 * a geo the campaign's own group forbids — and its 422 lands on the field it names.
 */
export function CreateCampaignForm() {
  const form = useForm({
    resolver: zodResolver(createCampaignSchema),
    defaultValues: EMPTY_CAMPAIGN,
  })

  // The picked offer's own record, beside the form and not inside it. The form's values are
  // the request body and the body carries an id; the combobox needs the whole record to draw
  // its label, which is a thing about the screen rather than a thing being submitted.
  const [offer, setOffer] = useState<Offer | null>(null)

  const create = useCreateCampaign()

  const submit = (values: CreateCampaignValues) => {
    create.mutate(values)
  }

  return (
    <form
      noValidate
      className="max-w-xl"
      onSubmit={(event) => {
        void form.handleSubmit(submit)(event)
      }}
    >
      {/* One `disabled` for the whole form while the tracker is being written to, which the
          browser applies to every control inside it — including the submit button. */}
      <FieldSet disabled={create.isPending}>
        <FieldGroup>
          <Controller
            control={form.control}
            name="name"
            render={({ field, fieldState }) => (
              <Field data-invalid={fieldState.invalid || undefined}>
                <FieldLabel htmlFor="campaign-name">Name</FieldLabel>
                <Input
                  {...field}
                  id="campaign-name"
                  autoComplete="off"
                  placeholder="Summer MX — Oxys"
                  aria-invalid={fieldState.invalid || undefined}
                />
                <FieldDescription>What the campaign is called in the tracker.</FieldDescription>
                <FieldError errors={[fieldState.error]} />
              </Field>
            )}
          />

          <Controller
            control={form.control}
            name="country"
            render={({ field, fieldState }) => (
              <Field data-invalid={fieldState.invalid || undefined}>
                <FieldLabel htmlFor="campaign-geo">Geo</FieldLabel>
                <GeoSelect
                  id="campaign-geo"
                  value={field.value}
                  onChange={field.onChange}
                  onBlur={field.onBlur}
                  invalid={fieldState.invalid}
                />
                <FieldDescription>
                  Flow 1 catches this country and sends it to google.com.
                </FieldDescription>
                <FieldError errors={[fieldState.error]} />
              </Field>
            )}
          />

          <Controller
            control={form.control}
            name="offer_id"
            render={({ field, fieldState }) => (
              <Field data-invalid={fieldState.invalid || undefined}>
                <FieldLabel htmlFor="campaign-offer">Offer</FieldLabel>
                <OfferCombobox
                  id="campaign-offer"
                  value={offer}
                  onChange={(picked) => {
                    setOffer(picked)
                    field.onChange(picked.id)
                  }}
                  onBlur={field.onBlur}
                  invalid={fieldState.invalid}
                />
                <FieldDescription>
                  Flow 2 rotates the offers and starts with this one at 100%.
                </FieldDescription>
                <FieldError errors={[fieldState.error]} />
              </Field>
            )}
          />

          <Field orientation="horizontal">
            <Button type="submit" size="lg">
              {create.isPending ? 'CREATING…' : 'CREATE'}
            </Button>
          </Field>
        </FieldGroup>
      </FieldSet>
    </form>
  )
}
