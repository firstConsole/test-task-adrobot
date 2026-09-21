import { createBrowserRouter } from 'react-router'

import { CampaignCreatePage } from '@/pages/campaign-create'
import { CampaignListPage } from '@/pages/campaign-list'
import { CampaignStreamsPage } from '@/pages/campaign-streams'
import { ROUTES } from '@/shared/config/routes'

import { AppLayout } from '../ui/app-layout'
import { NotFound } from '../ui/not-found'
import { RouteError } from '../ui/route-error'

const PAGES = [
  { path: ROUTES.campaignList, element: <CampaignListPage /> },
  { path: ROUTES.campaignCreate, element: <CampaignCreatePage /> },
  { path: ROUTES.campaignStreams, element: <CampaignStreamsPage /> },
  { path: '*', element: <NotFound /> },
]

export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    // The boundary goes on each page and not on the layout: React Router replaces the
    // element of the route that owns it, and a page that throws should not take the
    // header and the navigation down with it.
    children: PAGES.map((page) => ({ ...page, errorElement: <RouteError /> })),
  },
])
