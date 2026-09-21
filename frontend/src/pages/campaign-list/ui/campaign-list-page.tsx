import { SearchIcon } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'

import { CAMPAIGN_COLUMN_COUNT, CampaignRow, useCampaigns } from '@/entities/campaign'
import { ImportCampaignForm } from '@/features/import-campaign'
import { ApiError } from '@/shared/api/client'
import { ROUTES } from '@/shared/config/routes'
import { problemMessage } from '@/shared/lib/problem-message'
import { useDebouncedValue } from '@/shared/lib/use-debounced-value'
import { Button } from '@/shared/ui/button'
import { Input } from '@/shared/ui/input'
import { Skeleton } from '@/shared/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'

const DEBOUNCE_MS = 250
const SKELETON_ROWS = [0, 1, 2]

export function CampaignListPage() {
  const [term, setTerm] = useState('')
  const settled = useDebouncedValue(term, DEBOUNCE_MS)
  const campaigns = useCampaigns(settled)

  const rows = campaigns.data?.pages.flatMap((page) => page.campaigns) ?? []

  return (
    <section className="space-y-6">
      <div>
        <h2 className="text-lg font-medium">Campaigns</h2>
        <p className="text-muted-foreground mt-1 max-w-2xl text-sm">
          Everything this service has a mirror of — the campaigns it built and the ones it was
          handed. Open one to edit the offers its second flow rotates.
        </p>
      </div>

      <div className="flex flex-wrap items-end justify-between gap-6">
        <div className="min-w-64 flex-1">
          <label htmlFor="campaign-search" className="sr-only">
            Search campaigns
          </label>
          <div className="relative">
            <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
            <Input
              id="campaign-search"
              value={term}
              onChange={(event) => {
                setTerm(event.target.value)
              }}
              placeholder="Search by name or alias…"
              autoComplete="off"
              className="pl-8"
            />
          </div>
        </div>

        <ImportCampaignForm />
      </div>

      {campaigns.isError ? (
        <p role="alert" className="text-destructive text-sm">
          {problemMessage(campaigns.error, 'The campaigns could not be read.')}
          {campaigns.error instanceof ApiError && campaigns.error.correlationId !== null
            ? ` (${campaigns.error.correlationId})`
            : null}
        </p>
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Campaign</TableHead>
                <TableHead>Keitaro</TableHead>
                <TableHead>Geo</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="text-right">Tracker</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody className={campaigns.isPlaceholderData ? 'opacity-50' : undefined}>
              {campaigns.isPending
                ? SKELETON_ROWS.map((row) => (
                    <TableRow key={row}>
                      <TableCell colSpan={CAMPAIGN_COLUMN_COUNT}>
                        <Skeleton className="h-8 w-full" />
                      </TableCell>
                    </TableRow>
                  ))
                : null}

              {!campaigns.isPending && rows.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={CAMPAIGN_COLUMN_COUNT}
                    className="text-muted-foreground p-6 text-center text-sm"
                  >
                    {settled.trim() === '' ? (
                      <>
                        Nothing here yet.{' '}
                        <Link to={ROUTES.campaignCreate} className="underline underline-offset-4">
                          Build one
                        </Link>
                        , or open a campaign Keitaro already has by its id.
                      </>
                    ) : (
                      <>No campaign here matches “{settled.trim()}”.</>
                    )}
                  </TableCell>
                </TableRow>
              ) : null}

              {rows.map((campaign) => (
                <CampaignRow key={campaign.id} campaign={campaign} />
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Keyset paging, so this is "more", not "page 2": there are no page numbers to be
          wrong about when a campaign is created while somebody is reading. */}
      {campaigns.hasNextPage ? (
        <div className="flex justify-center">
          <Button
            type="button"
            variant="outline"
            disabled={campaigns.isFetchingNextPage}
            onClick={() => {
              void campaigns.fetchNextPage()
            }}
          >
            {campaigns.isFetchingNextPage ? 'READING…' : 'LOAD MORE'}
          </Button>
        </div>
      ) : null}
    </section>
  )
}
