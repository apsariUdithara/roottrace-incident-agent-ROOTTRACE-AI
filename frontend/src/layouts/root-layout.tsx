import { Link, Outlet } from 'react-router'

export function RootLayout() {
  return (
    <div className="flex min-h-svh flex-col">
      <header className="border-b px-6 py-3">
        <Link to="/" className="font-heading text-sm font-semibold tracking-tight">
          RootTrace <span className="text-primary">AI</span>
        </Link>
      </header>
      <main className="flex flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  )
}
