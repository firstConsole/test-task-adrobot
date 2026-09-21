import { cn } from 'cn'
import { Link, NavLink, Outlet } from 'react-router'

import { ROUTES } from '@/shared/config/routes'
import { Toaster } from '@/shared/ui/sonner'

const NAV = [
  { to: ROUTES.campaignList, label: 'Campaigns', end: true },
  { to: ROUTES.campaignCreate, label: 'New campaign', end: false },
]

export function AppLayout() {
  return (
    <div className="min-h-dvh">
      <header className="border-b">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-3">
          <Link to={ROUTES.campaignList} className="font-semibold tracking-tight">
            AD Robot
          </Link>
          <nav className="flex items-center gap-4 text-sm">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cn('hover:text-foreground', isActive ? 'text-foreground' : 'text-muted-foreground')
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-6">
        <Outlet />
      </main>
      <Toaster position="bottom-right" />
    </div>
  )
}
