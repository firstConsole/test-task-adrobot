import { zodResolver } from '@hookform/resolvers/zod'
import { DownloadIcon } from 'lucide-react'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'

import { ApiError } from '@/shared/api/client'
import { campaignStreamsPath } from '@/shared/config/routes'
import { readRefusals } from '@/shared/lib/refusals'
import { Button } from '@/shared/ui/button'
import { Field, FieldDescription, FieldError, FieldLabel } from '@/shared/ui/field'
import { Input } from '@/shared/ui/input'

import { useImportCampaign } from '../api/use-import-campaign'
import { FORM_FIELDS, importCampaignSchema } from '../model/schema'

/**
 * Open a campaign that is already in Keitaro, by the number in its URL.
 *
 * The interesting case is the second press. A campaign that is already open here comes back
 * as a 409 carrying the local id of the copy that exists — so the answer to "import 93212"
 * is to put the person in front of campaign 93212, which is what they wanted, rather than a
 * red line explaining that they cannot have it.
 */
export function ImportCampaignForm() {
  const form = useForm({
    resolver: zodResolver(importCampaignSchema),
    defaultValues: { keitaro_campaign_id: '' },
  })

  const adopt = useImportCampaign()
  const navigate = useNavigate()

  const submit = ({ keitaro_campaign_id: keitaroCampaignId }: { keitaro_campaign_id: number }) => {
    if (adopt.isPending) return

    adopt.mutate(keitaroCampaignId, {
      onSuccess: (campaign) => {
        toast.success(`${campaign.name} is open here now.`, {
          description: `Read from Keitaro campaign ${String(campaign.keitaro_campaign_id)}, with its flows.`,
        })
        void navigate(campaignStreamsPath(campaign.id))
      },
      onError: (error) => {
        // The 409 the API was built to answer with: it names the copy that already exists,
        // so this is a refusal that can be honoured instead of reported.
        const already = error instanceof ApiError ? error.problem.campaign_id : null
        if (already !== null && already !== undefined) {
          toast.info(`Campaign ${String(keitaroCampaignId)} was already open here.`)
          void navigate(campaignStreamsPath(already))
          return
        }

        const refusals = readRefusals(error, FORM_FIELDS)
        const [first] = refusals.fields
        const quoted = refusals.correlationId === null ? '' : ` (${refusals.correlationId})`

        form.setError(
          'keitaro_campaign_id',
          {
            type: 'server',
            // One field, so everything this refusal has to say says it here — a 404 from the
            // tracker included, which is the common one and reads perfectly under the input.
            message: first?.message ?? `${refusals.message ?? 'The import failed.'}${quoted}`,
          },
          { shouldFocus: true },
        )
      },
    })
  }

  return (
    <form
      noValidate
      aria-busy={adopt.isPending || undefined}
      onSubmit={(event) => {
        void form.handleSubmit(submit)(event)
      }}
    >
      <Field>
        <FieldLabel htmlFor="import-campaign-id">Open one from Keitaro</FieldLabel>
        <div className="flex items-start gap-2">
          <Input
            {...form.register('keitaro_campaign_id')}
            id="import-campaign-id"
            inputMode="numeric"
            autoComplete="off"
            placeholder="93212"
            disabled={adopt.isPending}
            aria-invalid={form.formState.errors.keitaro_campaign_id !== undefined || undefined}
            className="w-32"
          />
          <Button type="submit" variant="outline" disabled={adopt.isPending}>
            <DownloadIcon />
            {adopt.isPending ? 'READING…' : 'IMPORT'}
          </Button>
        </div>
        <FieldDescription>
          A campaign built by hand, or by somebody else, with everything already in it.
        </FieldDescription>
        <FieldError errors={[form.formState.errors.keitaro_campaign_id]} />
      </Field>
    </form>
  )
}
