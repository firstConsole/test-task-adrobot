import { useParams } from 'react-router'

export function CampaignStreamsPage() {
  const { campaignId } = useParams<'campaignId'>()

  return (
    <section>
      <h2 className="text-lg font-medium">Streams</h2>
      <p className="text-muted-foreground mt-1 text-sm">
        The editor lands at stage 10. Campaign <code>{campaignId}</code>.
      </p>
    </section>
  )
}
