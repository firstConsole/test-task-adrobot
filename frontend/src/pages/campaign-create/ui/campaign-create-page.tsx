import { CreateCampaignForm } from '@/features/create-campaign'
import { SyncOffersButton } from '@/features/sync-offers'

export function CampaignCreatePage() {
  return (
    <section className="space-y-6">
      <div>
        <h2 className="text-lg font-medium">Новая кампания</h2>
        <p className="text-muted-foreground mt-1 max-w-xl text-sm">
          Два потока в Keitaro. Flow 1 ловит указанное ниже гео и уводит его на google.com;
          Flow 2 крутит офферы и начинает с одного выбранного, на 100%.
        </p>
      </div>

      <CreateCampaignForm offerEmpty={<SyncOffersButton />} />
    </section>
  )
}
