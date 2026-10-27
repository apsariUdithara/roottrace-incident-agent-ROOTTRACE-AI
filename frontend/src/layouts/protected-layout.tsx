import { Navigate, Outlet, useLocation } from 'react-router'

import { loginPath, useSession } from '@/lib/auth'

/** Wraps the pages that need a login. The route's loader (`requireSession`) checks on arrival;
 * this follows the session afterwards, so logging out or an expired session leads to the login
 * page straight away. */
export function ProtectedLayout() {
  const session = useSession()
  const { pathname, search } = useLocation()

  if (!session) return <Navigate to={loginPath(pathname + search)} replace />
  return <Outlet />
}
