import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { useNavigate } from 'react-router'

import type { Offer } from '@/entities/offer'
import { OfferCombobox } from '@/entities/offer'
import { campaignStreamsPath } from '@/shared/config/routes'
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
import { readRefusals } from '../model/refusals'
import type { CreateCampaignValues } from '../model/schema'
import { EMPTY_CAMPAIGN, createCampaignSchema } from '../model/schema'
import { campaignCreatedToast } from './created-toast'
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
  const navigate = useNavigate()

  const submit = (values: CreateCampaignValues) => {
    // The whole form is disabled while a creation is in flight, so this can only be reached
    // by a second Enter racing the first render. A campaign is not a thing to make twice.
    if (create.isPending) return

    form.clearErrors('root')

    create.mutate(values, {
      onSuccess: (campaign) => {
        campaignCreatedToast(campaign, values.country)
        // Straight into the editor, which is where part 1 hands over to part 2: the campaign
        // now has one offer on 100% and the next thing anyone does to it is add a second.
        // A campaign whose flows the tracker refused goes there too — that screen is the one
        // that says so, and the one with the button to read the tracker again.
        void navigate(campaignStreamsPath(campaign.id))
      },
      onError: (error) => {
        const refusals = readRefusals(error)

        refusals.fields.forEach(({ field, message }, index) => {
          // The first one takes the focus, which is also where a screen reader is put.
          form.setError(field, { type: 'server', message }, { shouldFocus: index === 0 })
        })

        if (refusals.message !== null) {
          const quoted =
            refusals.correlationId === null ? '' : ` (${refusals.correlationId})`
          form.setError('root', { type: 'server', message: `${refusals.message}${quoted}` })
        }
      },
    })
  }

  return (
    <form
      noValidate
      aria-busy={create.isPending || undefined}
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
                  ref={field.ref}
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
                  ref={field.ref}
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

          {/* What is left of a refusal once every complaint that named a field is sitting
              under that field: a 502 from the tracker, a 401, a location this form has no
              input for. It stays on screen — unlike a toast — because it is the answer to
              "why is there no campaign", and it carries the id to quote when asking. */}
          <FieldError errors={[form.formState.errors.root]} />

          <Field orientation="horizontal">
            <Button type="submit" size="lg">
              {create.isPending ? 'CREATING…' : 'CREATE'}
            </Button>
            {create.isPending ? (
              <span role="status" className="text-muted-foreground text-sm">
                Writing the campaign and its two flows to Keitaro…
              </span>
            ) : null}
          </Field>
        </FieldGroup>
      </FieldSet>
    </form>
  )
}
