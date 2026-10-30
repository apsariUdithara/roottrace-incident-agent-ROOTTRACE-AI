import type { ReactNode } from 'react'

import { Label } from '@/components/ui/label'

/** A labelled form control, with an optional hint under it. Give the control the same `id`, and
 * with a hint, `aria-describedby={`${id}-hint`}`. */
export function FormField({
  id,
  label,
  hint,
  children,
}: {
  id: string
  label: string
  hint?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && (
        <p id={`${id}-hint`} className="text-[0.8125rem] text-muted-foreground">
          {hint}
        </p>
      )}
    </div>
  )
}
