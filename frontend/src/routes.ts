import type { RouteObject } from 'react-router'

import { LoadingScreen } from '@/components/loading-screen'
import { AppShell } from '@/layouts/app-shell'
import { AuthLayout } from '@/layouts/auth-layout'
import { ProtectedLayout } from '@/layouts/protected-layout'
import { guestOnly, requireSession } from '@/lib/auth'
import { DashboardPage } from '@/pages/dashboard-page'
import { EvaluationPage } from '@/pages/evaluation-page'
import { KnowledgePage } from '@/pages/knowledge-page'
import { LoginPage } from '@/pages/login-page'
import { LogsPage } from '@/pages/logs-page'
import { NotFoundPage } from '@/pages/not-found-page'
import { ProjectSettingsPage } from '@/pages/project-settings-page'
import { ProjectsPage } from '@/pages/projects-page'
import { RegisterPage } from '@/pages/register-page'

// Kept apart from router.ts so tests can mount the same routes in a memory router.
export const routes: RouteObject[] = [
  {
    path: '/',
    children: [
      {
        Component: AuthLayout,
        children: [
          { path: 'login', Component: LoginPage, loader: guestOnly, HydrateFallback: LoadingScreen },
          {
            path: 'register',
            Component: RegisterPage,
            loader: guestOnly,
            HydrateFallback: LoadingScreen,
          },
          { path: '*', Component: NotFoundPage },
        ],
      },
      {
        // Every page that needs a login goes in here, inside the app shell.
        Component: ProtectedLayout,
        loader: requireSession,
        HydrateFallback: LoadingScreen,
        children: [
          {
            Component: AppShell,
            children: [
              { index: true, Component: DashboardPage },
              { path: 'projects', Component: ProjectsPage },
              { path: 'projects/:projectId', Component: ProjectSettingsPage },
              { path: 'knowledge', Component: KnowledgePage },
              { path: 'logs', Component: LogsPage },
              { path: 'evaluation', Component: EvaluationPage },
              // Loaded when first opened: Markdown rendering is heavy, and only this page needs it.
              {
                path: 'chat',
                lazy: async () => ({ Component: (await import('@/pages/chat-page')).ChatPage }),
              },
            ],
          },
        ],
      },
    ],
  },
]
