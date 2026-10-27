// The project the app is working in, chosen in the top bar's switcher. Pages that are about one
// project (investigations, knowledge) read it with useCurrentProject().
import { useQuery } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'

import { api, ok } from '@/api/client'

const STORAGE_KEY = 'roottrace.project'
const listeners = new Set<() => void>()
// Remembered per browser, for convenience only. Where storage is blocked, the choice is kept
// here instead: it still switches, but a reload starts again from the newest project.
let unsaved: string | null = null

function storedProjectId(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

export function selectProject(projectId: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, projectId)
    unsaved = null
  } catch {
    unsaved = projectId
  }
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useProjects() {
  return useQuery({ queryKey: ['projects'], queryFn: () => ok(api.GET('/projects')) })
}

/** The user's projects and the selected one. If nothing is selected yet, or the stored project
 * isn't this user's (someone else used this browser), it's the newest project; null if none. */
export function useCurrentProject() {
  const selectedId = useSyncExternalStore(subscribe, () => unsaved ?? storedProjectId())
  const { data: projects = [], isPending, isError } = useProjects()
  const project = projects.find((p) => p.id === selectedId) ?? projects[0] ?? null
  return { projects, project, isPending, isError }
}
