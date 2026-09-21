import { Link, useRouteError } from 'react-router'

import { ApiError } from '@/shared/api/client'
import { ROUTES } from '@/shared/config/routes'
import { Button } from '@/shared/ui/button'

export function RouteError() {
  const error = useRouteError()
  const problem = error instanceof ApiError ? error.problem : null

  return (
    <section>
      <h2 className="text-lg font-medium">{problem?.title ?? 'Something went wrong'}</h2>
      <p className="text-muted-foreground mt-1 text-sm">
        {problem?.detail ?? (error instanceof Error ? error.message : 'The screen could not be drawn.')}
      </p>
      {problem?.correlation_id != null && (
        <p className="text-muted-foreground mt-1 text-xs">
          Quote <code>{problem.correlation_id}</code> in a bug report.
        </p>
      )}
      <Button asChild variant="outline" size="sm" className="mt-4">
        <Link to={ROUTES.campaignList}>Back to the campaigns</Link>
      </Button>
    </section>
  )
}
