import { Link } from 'react-router'

import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { selectProject, useCurrentProject } from '@/lib/current-project'

/** The top bar's project picker: what the app is working in. */
export function ProjectSwitcher() {
  const { projects, project, isPending, isError } = useCurrentProject()

  if (isPending) return <Skeleton className="h-6 w-40" />
  if (isError) return <span className="text-[0.8125rem] text-destructive">Couldn't load your projects</span>
  if (!project) {
    return (
      <Link to="/projects" className="text-[0.8125rem] text-primary hover:underline">
        Create a project
      </Link>
    )
  }
  return (
    <NativeSelect
      size="sm"
      aria-label="Project"
      value={project.id}
      onChange={(event) => selectProject(event.target.value)}
    >
      {projects.map((p) => (
        <NativeSelectOption key={p.id} value={p.id}>
          {p.name}
        </NativeSelectOption>
      ))}
    </NativeSelect>
  )
}
