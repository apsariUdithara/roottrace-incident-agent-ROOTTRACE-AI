import { LogOut, Moon, Sun } from 'lucide-react'
import { Outlet } from 'react-router'

import { AppSidebar } from '@/components/app-sidebar'
import { ProjectSwitcher } from '@/components/project-switcher'
import { SourceViewerProvider } from '@/components/source-viewer'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { SidebarInset, SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar'
import { TooltipProvider } from '@/components/ui/tooltip'
import { logout, useSession } from '@/lib/auth'
import { setTheme, useTheme } from '@/lib/theme'

/** The logged-in app: sidebar navigation, and a top bar with the project switcher and the
 * user. The sidebar collapses to icons with its button, or Ctrl/⌘+B. Every page can open a
 * source in the source viewer's drawer (useSourceViewer). */
export function AppShell() {
  const session = useSession()
  const theme = useTheme()

  return (
    <TooltipProvider>
      <SourceViewerProvider>
        <SidebarProvider>
          <AppSidebar />
          <SidebarInset>
            <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b bg-background/80 px-4 backdrop-blur">
              <SidebarTrigger />
              <Separator orientation="vertical" className="mx-1 h-4" />
              <ProjectSwitcher />
              <div className="ml-auto flex items-center gap-2">
                <span className="hidden text-muted-foreground sm:inline">{session?.user.email}</span>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
                  onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
                >
                  {theme === 'dark' ? <Sun /> : <Moon />}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="hover:bg-red-500/10 hover:text-red-700 dark:hover:text-red-300"
                  onClick={() => void logout()}
                >
                  <LogOut data-icon="inline-start" />
                  Log out
                </Button>
              </div>
            </header>
            <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col px-4 py-6 sm:px-8 sm:py-8">
              <Outlet />
            </div>
          </SidebarInset>
        </SidebarProvider>
      </SourceViewerProvider>
    </TooltipProvider>
  )
}
