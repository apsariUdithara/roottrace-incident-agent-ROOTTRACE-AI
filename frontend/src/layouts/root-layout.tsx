import { Link, Outlet } from 'react-router'

import { Button } from '@/components/ui/button'
import { logout, useSession } from '@/lib/auth'

export function RootLayout() {
  const session = useSession()

  return (
    <div className="flex min-h-svh flex-col">
      <header className="flex items-center justify-between border-b px-6 py-3">
        <Link to="/" className="font-heading text-sm font-semibold tracking-tight">
          RootTrace <span className="text-primary">AI</span>
        </Link>
        {session && (
          <div className="flex items-center gap-3 text-xs">
            <span className="text-muted-foreground">{session.user.email}</span>
            <Button variant="ghost" size="sm" onClick={() => void logout()}>
              Log out
            </Button>
          </div>
        )}
      </header>
      <main className="flex flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  )
}
