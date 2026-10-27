import type { RouteObject } from 'react-router'

import { LoadingScreen } from '@/components/loading-screen'
import { ProtectedLayout } from '@/layouts/protected-layout'
import { RootLayout } from '@/layouts/root-layout'
import { guestOnly, requireSession } from '@/lib/auth'
import { HomePage } from '@/pages/home-page'
import { LoginPage } from '@/pages/login-page'
import { NotFoundPage } from '@/pages/not-found-page'
import { RegisterPage } from '@/pages/register-page'

// Kept apart from router.tsx so tests can mount the same routes in a memory router.
export const routes: RouteObject[] = [
  {
    path: '/',
    Component: RootLayout,
    children: [
      { path: 'login', Component: LoginPage, loader: guestOnly, HydrateFallback: LoadingScreen },
      {
        path: 'register',
        Component: RegisterPage,
        loader: guestOnly,
        HydrateFallback: LoadingScreen,
      },
      {
        // Every page that needs a login goes in here.
        Component: ProtectedLayout,
        loader: requireSession,
        HydrateFallback: LoadingScreen,
        children: [{ index: true, Component: HomePage }],
      },
      { path: '*', Component: NotFoundPage },
    ],
  },
]
