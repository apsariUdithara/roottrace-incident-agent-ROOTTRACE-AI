import { useQuery } from '@tanstack/react-query'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

type Health = { status: string }

async function fetchHealth(): Promise<Health> {
  const response = await fetch('/api/health')
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json()
}

function ApiStatus() {
  const { data, isPending, isError } = useQuery({ queryKey: ['health'], queryFn: fetchHealth })

  if (isPending) return <Badge variant="secondary">checking…</Badge>
  if (isError) return <Badge variant="destructive">unreachable</Badge>
  return <Badge>{data.status}</Badge>
}

export function HomePage() {
  return (
    <div className="flex flex-1 items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>RootTrace AI</CardTitle>
          <CardDescription>
            Evidence-backed incident investigation with a tested fix.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">Backend API</span>
          <ApiStatus />
        </CardContent>
      </Card>
    </div>
  )
}
