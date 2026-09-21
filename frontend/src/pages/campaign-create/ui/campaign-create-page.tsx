import { CreateCampaignForm } from '@/features/create-campaign'
import { SyncOffersButton } from '@/features/sync-offers'

export function CampaignCreatePage() {
  return (
    <section className="space-y-6">
      <div>
        <h2 className="text-lg font-medium">New campaign</h2>
        <p className="text-muted-foreground mt-1 max-w-xl text-sm">
          Two flows in Keitaro. Flow 1 catches the geo below and sends it to google.com; Flow 2
          rotates the offers and starts with the one offer you pick, on 100%.
        </p>
      </div>

      <CreateCampaignForm offerEmpty={<SyncOffersButton />} />
    </section>
  )
}
