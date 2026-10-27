import { LogOut } from 'lucide-react'
import { Outlet } from 'react-router'

import { AppSidebar } from '@/components/app-sidebar'
import { ProjectSwitcher } from '@/components/project-switcher'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { SidebarInset, SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar'
import { TooltipProvider } from '@/components/ui/tooltip'
import { logout, useSession } from '@/lib/auth'

/** The logged-in app: sidebar navigation, and a top bar with the project switcher and the
 * user. The sidebar collapses to icons with its button, or Ctrl/⌘+B. */
export function AppShell() {
  const session = useSession()

  return (
    <TooltipProvider>
      <SidebarProvider>
        <AppSidebar />
        <SidebarInset>
          <header className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
            <SidebarTrigger />
            <Separator orientation="vertical" className="mx-1 h-4" />
            <ProjectSwitcher />
            <div className="ml-auto flex items-center gap-3 text-xs">
              <span className="text-muted-foreground">{session?.user.email}</span>
              <Button variant="ghost" size="sm" onClick={() => void logout()}>
                <LogOut data-icon="inline-start" />
                Log out
              </Button>
            </div>
          </header>
          <div className="flex flex-1 flex-col p-6">
            <Outlet />
          </div>
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}
