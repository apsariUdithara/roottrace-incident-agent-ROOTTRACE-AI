import {
  BookOpen,
  FlaskConical,
  FolderGit2,
  LayoutDashboard,
  MessageSquare,
  type LucideIcon,
} from 'lucide-react'
import { Link, NavLink, useMatch } from 'react-router'

import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from '@/components/ui/sidebar'

type NavItem = { to: string; label: string; icon: LucideIcon; end?: boolean }

const NAV: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/projects', label: 'Projects', icon: FolderGit2 },
  { to: '/knowledge', label: 'Knowledge', icon: BookOpen },
  { to: '/evaluation', label: 'Evaluation', icon: FlaskConical },
  { to: '/chat', label: 'Chat', icon: MessageSquare },
]

export function AppSidebar() {
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="h-12 justify-center border-b px-4">
        <Link to="/" className="truncate font-heading text-sm font-semibold tracking-tight">
          RootTrace <span className="text-primary">AI</span>
        </Link>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarMenu>
            {NAV.map((item) => (
              <NavItemLink key={item.to} {...item} />
            ))}
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  )
}

function NavItemLink({ to, label, icon: Icon, end = false }: NavItem) {
  const active = useMatch({ path: to, end }) !== null

  return (
    <SidebarMenuItem>
      {/* NavLink also sets aria-current="page" on the active item */}
      <SidebarMenuButton render={<NavLink to={to} end={end} />} isActive={active} tooltip={label}>
        <Icon />
        <span>{label}</span>
      </SidebarMenuButton>
    </SidebarMenuItem>
  )
}
