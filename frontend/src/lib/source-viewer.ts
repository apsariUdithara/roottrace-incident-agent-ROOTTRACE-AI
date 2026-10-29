// The source viewer: one drawer in the app shell (components/source-viewer.tsx) that any page
// opens to show what a source_ref points to: the code, commit, doc, log lines, incident, issue or
// memory behind a claim.
import { createContext, useContext } from 'react'

/** Opens `sourceRef` from the project `projectId` in the drawer. */
export type OpenSource = (projectId: string, sourceRef: string) => void

export const SourceViewerContext = createContext<OpenSource | null>(null)

/** The function that opens a ref in the source viewer. Only under the app shell. */
export function useSourceViewer(): OpenSource {
  const open = useContext(SourceViewerContext)
  if (!open) throw new Error('useSourceViewer() needs the app shell (SourceViewerProvider)')
  return open
}
