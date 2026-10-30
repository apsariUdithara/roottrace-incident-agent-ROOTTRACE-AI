// /: how the agent is doing in the current project, and where to pick up.
import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { Link } from 'react-router'

import { api, ok } from '@/api/client'
import { PageHeader } from '@/components/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useCurrentProject } from '@/lib/current-project'
import { errorMessage } from '@/lib/errors'
import { formatUtc } from '@/lib/time'

export function DashboardPage() {
  const { project } = useCurrentProject()
  const stats = useQuery({
    queryKey: ['stats', 'dashboard', project?.id],
    queryFn: () => ok(api.GET('/stats/dashboard', { params: { query: { project_id: project?.id } } })),
  })
  const s = stats.data

  return (
    <div className="flex max-w-5xl flex-col gap-6">
      <PageHeader
        title="Dashboard"
        description={project ? `Investigations in ${project.name}.` : 'Investigations across your projects.'}
      />
      {stats.isPending && <Skeleton className="h-24" />}
      {stats.isError && <p role="alert" className="text-xs text-destructive">{errorMessage(stats.error)}</p>}
      {s && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Tile label="Investigations" value={String(s.investigations)} />
            <Tile label="Avg confidence" value={pct(s.avg_confidence)} />
            <Tile label="Avg time" value={s.avg_duration_ms == null ? '—' : `${Math.round(s.avg_duration_ms / 1000)} s`} />
            <Tile label="Avg cost" value={s.avg_cost_usd == null ? '—' : `$${s.avg_cost_usd.toFixed(3)}`} />
            <Tile label="Fixes verified" value={pct(s.patch_success_rate)} hint="patches that passed in the sandbox" />
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Recent investigations</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-2">
                {s.recent_investigations.map((inv) => (
                  <Link key={inv.id} to={`/investigations/${inv.id}`} className="flex items-center gap-2 rounded-md p-1.5 text-sm hover:bg-muted">
                    <Badge variant={inv.status === 'failed' ? 'destructive' : 'outline'}>{inv.status}</Badge>
                    <span className="flex-1 truncate">{inv.question}</span>
                    {inv.confidence != null && <span className="text-xs">{pct(inv.confidence)}</span>}
                  </Link>
                ))}
                {s.recent_investigations.length === 0 && (
                  <p className="text-sm text-muted-foreground">None yet.</p>
                )}
                <Button size="sm" className="mt-2 w-fit" render={<Link to="/investigations" />}>
                  <Search data-icon="inline-start" />
                  Investigate an incident
                </Button>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Recent incidents</CardTitle>
                <CardDescription>Past incidents the agent can match against.</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-2">
                {s.recent_incidents.map((inc) => (
                  <div key={inc.id} className="flex items-center gap-2 text-sm">
                    <code className="text-xs">{inc.key}</code>
                    <span className="flex-1 truncate">{inc.title}</span>
                    {inc.occurred_at && <span className="text-xs text-muted-foreground">{formatUtc(inc.occurred_at)}</span>}
                  </div>
                ))}
                {s.recent_incidents.length === 0 && <p className="text-sm text-muted-foreground">None yet.</p>}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl">{value}</CardTitle>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </CardHeader>
    </Card>
  )
}

const pct = (value: number | null) => (value == null ? '—' : `${Math.round(value * 100)}%`)
