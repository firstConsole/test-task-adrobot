import { zodResolver } from '@hookform/resolvers/zod'
import { TriangleAlertIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { useState } from 'react'
import { Controller, useForm, useWatch } from 'react-hook-form'
import { useNavigate } from 'react-router'

import type { Offer } from '@/entities/offer'
import { OfferCombobox } from '@/entities/offer'
import { campaignStreamsPath } from '@/shared/config/routes'
import { readRefusals } from '@/shared/lib/refusals'
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
import { geoWarning } from '../model/geo-warning'
import type { CreateCampaignValues } from '../model/schema'
import { EMPTY_CAMPAIGN, FORM_FIELDS, createCampaignSchema } from '../model/schema'
import { campaignCreatedToast } from './created-toast'
import { GeoSelect } from './geo-select'

/**
 * Part 1's form: a name, a geo and an offer, and Keitaro has a campaign.
 *
 * Validated twice on purpose. This schema answers the obvious refusals without a round trip;
 * the API refuses again, over rules only it can hold — an offer that is not in the tracker,
 * a geo the campaign's own group forbids — and its 422 lands on the field it names.
 */
/**
 * `offerEmpty` is whatever should be offered when the catalogue answers with nothing — the
 * button that reads it from Keitaro. It arrives as a prop because that button is a feature
 * and so is this form: sibling slices do not import each other, so the page hands it over.
 */
export function CreateCampaignForm({ offerEmpty }: { offerEmpty?: ReactNode } = {}) {
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

  // Subscribed to rather than read off `getValues`, because this has to redraw when either
  // half of the pair changes — and the offer is usually the half that changes last.
  const country = useWatch({ control: form.control, name: 'country' })
  const mismatch = geoWarning(country, offer)

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
        const refusals = readRefusals(error, FORM_FIELDS)

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
                <FieldLabel htmlFor="campaign-name">Имя</FieldLabel>
                <Input
                  {...field}
                  id="campaign-name"
                  autoComplete="off"
                  placeholder="Summer MX — Oxys"
                  aria-invalid={fieldState.invalid || undefined}
                />
                <FieldDescription>Как кампания называется в трекере.</FieldDescription>
                <FieldError errors={[fieldState.error]} />
              </Field>
            )}
          />

          <Controller
            control={form.control}
            name="country"
            render={({ field, fieldState }) => (
              <Field data-invalid={fieldState.invalid || undefined}>
                <FieldLabel htmlFor="campaign-geo">Гео</FieldLabel>
                <GeoSelect
                  id="campaign-geo"
                  ref={field.ref}
                  value={field.value}
                  onChange={field.onChange}
                  onBlur={field.onBlur}
                  invalid={fieldState.invalid}
                />
                <FieldDescription>
                  Flow 1 ловит эту страну и уводит её на google.com.
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
                <FieldLabel htmlFor="campaign-offer">Оффер</FieldLabel>
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
                  empty={offerEmpty}
                />
                <FieldDescription>
                  Flow 2 крутит офферы и начинает с этого, на 100%.
                </FieldDescription>
                <FieldError errors={[fieldState.error]} />

                {/* A warning and not an error: the campaign is buildable, it just would not
                    do anything. Amber and a triangle rather than red, and the CREATE button
                    stays live — a buyer who means it can mean it. */}
                {mismatch === null ? null : (
                  <p
                    role="status"
                    className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
                  >
                    <TriangleAlertIcon className="mt-0.5 size-4 shrink-0" />
                    <span>{mismatch}</span>
                  </p>
                )}
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
                Пишу кампанию и два её потока в Keitaro…
              </span>
            ) : null}
          </Field>
        </FieldGroup>
      </FieldSet>
    </form>
  )
}
