import type { ReactNode } from 'react'

import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export function PageHeader({ title, description }: { title: string; description?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-1">
      <h1 className="font-heading text-xl font-semibold tracking-tight">{title}</h1>
      {description && <p className="text-sm text-muted-foreground">{description}</p>}
    </div>
  )
}

/** A page whose place in the app exists but whose content is still to be built. Like the
 * backend's 501 stubs, it says which backlog task builds it, rather than showing fake data. */
export function PlannedPage({
  title,
  task,
  description,
}: {
  title: string
  task: string
  description: string
}) {
  return (
    <>
      <PageHeader title={title} />
      <Card className="max-w-lg">
        <CardHeader>
          <CardTitle>Not built yet</CardTitle>
          <CardDescription>
            {description} Coming with {task}.
          </CardDescription>
        </CardHeader>
      </Card>
    </>
  )
}
