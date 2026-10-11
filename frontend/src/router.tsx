import { createBrowserRouter } from 'react-router'

import { RootLayout } from '@/layouts/root-layout'
import { HomePage } from '@/pages/home-page'
import { NotFoundPage } from '@/pages/not-found-page'

export const router = createBrowserRouter([
  {
    path: '/',
    Component: RootLayout,
    children: [
      { index: true, Component: HomePage },
      { path: '*', Component: NotFoundPage },
    ],
  },
])
