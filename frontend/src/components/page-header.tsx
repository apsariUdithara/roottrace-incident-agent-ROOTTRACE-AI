import type { ReactNode } from 'react'

export function PageHeader({ title, description }: { title: string; description?: ReactNode }) {
  return (
    <div className="mb-8 flex flex-col gap-1.5">
      <h1 className="font-heading text-2xl font-semibold tracking-tight">{title}</h1>
      {description && <p className="max-w-3xl text-[0.9375rem] text-muted-foreground">{description}</p>}
    </div>
  )
}
