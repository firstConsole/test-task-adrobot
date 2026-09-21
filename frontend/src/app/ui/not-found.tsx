import { Link } from 'react-router'

import { ROUTES } from '@/shared/config/routes'

export function NotFound() {
  return (
    <section>
      <h2 className="text-lg font-medium">Такой страницы нет</h2>
      <Link to={ROUTES.campaignList} className="text-muted-foreground mt-1 block text-sm underline">
        К кампаниям
      </Link>
    </section>
  )
}
