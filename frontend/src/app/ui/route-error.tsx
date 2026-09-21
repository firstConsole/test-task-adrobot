import { Link, useRouteError } from 'react-router'

import { ApiError } from '@/shared/api/client'
import { ROUTES } from '@/shared/config/routes'
import { Button } from '@/shared/ui/button'

export function RouteError() {
  const error = useRouteError()
  const problem = error instanceof ApiError ? error.problem : null

  return (
    <section>
      <h2 className="text-lg font-medium">{problem?.title ?? 'Что-то пошло не так'}</h2>
      <p className="text-muted-foreground mt-1 text-sm">
        {problem?.detail ?? (error instanceof Error ? error.message : 'Экран не удалось отрисовать.')}
      </p>
      {problem?.correlation_id != null && (
        <p className="text-muted-foreground mt-1 text-xs">
          Укажите <code>{problem.correlation_id}</code> в баг-репорте.
        </p>
      )}
      <Button asChild variant="outline" size="sm" className="mt-4">
        <Link to={ROUTES.campaignList}>К кампаниям</Link>
      </Button>
    </section>
  )
}
