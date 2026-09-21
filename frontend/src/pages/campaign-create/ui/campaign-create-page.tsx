import { CreateCampaignForm } from '@/features/create-campaign'
import { SyncOffersButton } from '@/features/sync-offers'

export function CampaignCreatePage() {
  return (
    <section className="space-y-6">
      <div>
        <h2 className="text-lg font-medium">Новая кампания</h2>
        <p className="text-muted-foreground mt-1 max-w-xl text-sm">
          Создайте кампанию, чтобы начать продвижение ваших офферов. 
          Выберите оффер из списка и настройте параметры кампании в соответствии с вашими целями.
        </p>
      </div>

      <CreateCampaignForm offerEmpty={<SyncOffersButton />} />
    </section>
  )
}
