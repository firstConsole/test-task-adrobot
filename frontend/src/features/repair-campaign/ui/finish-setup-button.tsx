import { WrenchIcon } from 'lucide-react'

import { Button } from '@/shared/ui/button'

import { useRepairCampaign } from '../api/use-repair-campaign'

/** `FINISH SETUP`: create the flows the tracker is missing, and leave the rest alone. */
export function FinishSetupButton({ campaignId }: { campaignId: string }) {
  const repair = useRepairCampaign(campaignId)

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      disabled={repair.finishing}
      onClick={repair.finishSetup}
    >
      <WrenchIcon />
      {repair.finishing ? 'FINISHING…' : 'FINISH SETUP'}
    </Button>
  )
}
