import { createBrowserRouter } from 'react-router'

import { CampaignCreatePage } from '@/pages/campaign-create'
import { CampaignListPage } from '@/pages/campaign-list'
import { CampaignStreamsPage } from '@/pages/campaign-streams'
import { ROUTES } from '@/shared/config/routes'

import { AppLayout } from '../ui/app-layout'
import { NotFound } from '../ui/not-found'

export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    children: [
      { path: ROUTES.campaignList, element: <CampaignListPage /> },
      { path: ROUTES.campaignCreate, element: <CampaignCreatePage /> },
      { path: ROUTES.campaignStreams, element: <CampaignStreamsPage /> },
      { path: '*', element: <NotFound /> },
    ],
  },
])
