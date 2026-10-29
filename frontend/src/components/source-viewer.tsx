import { useQuery } from '@tanstack/react-query'
import { useCallback, useState, type ReactNode } from 'react'

import { api, ok } from '@/api/client'
import { SOURCE_ICONS } from '@/components/source-chip'
import { SourceView } from '@/components/source-views'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { errorMessage } from '@/lib/errors'
import { describeRef } from '@/lib/source-refs'
import { SourceViewerContext } from '@/lib/source-viewer'

type Opened = { projectId: string; sourceRef: string }

/** Gives the pages under it useSourceViewer(), and holds the one drawer it opens. */
export function SourceViewerProvider({ children }: { children: ReactNode }) {
  // The last ref stays after closing, so the drawer doesn't empty while it slides away.
  const [opened, setOpened] = useState<Opened | null>(null)
  const [isOpen, setIsOpen] = useState(false)
  const open = useCallback((projectId: string, sourceRef: string) => {
    setOpened({ projectId, sourceRef })
    setIsOpen(true)
  }, [])

  return (
    <SourceViewerContext.Provider value={open}>
      {children}
      <Sheet open={isOpen} onOpenChange={setIsOpen}>
        <SheetContent side="right" className="w-full gap-0 data-[side=right]:sm:max-w-3xl">
          {opened && <SourcePanel key={`${opened.projectId} ${opened.sourceRef}`} {...opened} />}
        </SheetContent>
      </Sheet>
    </SourceViewerContext.Provider>
  )
}

function SourcePanel({ projectId, sourceRef }: Opened) {
  const summary = describeRef(sourceRef)
  const Icon = SOURCE_ICONS[summary.kind ?? 'doc']
  const source = useQuery({
    queryKey: ['projects', projectId, 'sources', sourceRef],
    queryFn: () =>
      ok(
        api.GET('/projects/{project_id}/sources', {
          params: { path: { project_id: projectId }, query: { ref: sourceRef } },
        }),
      ),
  })

  return (
    <>
      <SheetHeader className="border-b pr-12">
        <SheetTitle className="flex items-center gap-2">
          <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <span className="truncate">{summary.short}</span>
        </SheetTitle>
        <SheetDescription className="font-mono break-all">{sourceRef}</SheetDescription>
      </SheetHeader>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-6">
        {source.isPending && <Skeleton className="h-64" />}
        {source.isError && (
          <p role="alert" className="text-xs text-destructive">
            {errorMessage(source.error)}
          </p>
        )}
        {source.data && <SourceView source={source.data} projectId={projectId} />}
      </div>
    </>
  )
}
