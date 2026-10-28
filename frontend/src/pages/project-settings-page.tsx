import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { FormEvent } from 'react'
import { Link, useParams } from 'react-router'

import { api, ok } from '@/api/client'
import type { Schemas } from '@/api/types'
import { FormField } from '@/components/form-field'
import { PageHeader } from '@/components/page-header'
import { RepositorySettings } from '@/components/repository-settings'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { useProjects } from '@/lib/current-project'
import { errorMessage } from '@/lib/errors'

type Project = Schemas['ProjectOut']

/** /projects/:projectId: a project's name and description, and its repository. */
export function ProjectSettingsPage() {
  const { projectId } = useParams()
  const { data: projects, isPending, isError } = useProjects()
  const project = projects?.find((p) => p.id === projectId)

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <Link to="/projects" className="text-xs text-muted-foreground hover:underline">
        ← All projects
      </Link>
      {isPending && <Skeleton className="h-32" />}
      {isError && (
        <p role="alert" className="text-xs text-destructive">
          Couldn't load this project. Reload the page to try again.
        </p>
      )}
      {projects && !project && (
        <Card className="max-w-lg">
          <CardHeader>
            <CardTitle>Project not found</CardTitle>
            <CardDescription>It may have been deleted, or the link is wrong.</CardDescription>
          </CardHeader>
        </Card>
      )}
      {project && (
        <>
          <PageHeader title={project.name} description="Project settings" />
          <ProjectDetailsForm key={project.id} project={project} />
          <RepositorySettings projectId={project.id} />
        </>
      )}
    </div>
  )
}

function ProjectDetailsForm({ project }: { project: Project }) {
  const queryClient = useQueryClient()
  const save = useMutation({
    mutationFn: (body: Schemas['ProjectUpdate']) =>
      ok(api.PATCH('/projects/{project_id}', { params: { path: { project_id: project.id } }, body })),
    onSuccess: (saved) =>
      queryClient.setQueryData<Project[]>(['projects'], (projects = []) =>
        projects.map((p) => (p.id === saved.id ? saved : p)),
      ),
  })

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    save.mutate({ name: String(form.get('name')), description: String(form.get('description')) })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Details</CardTitle>
      </CardHeader>
      {/* Any edit clears the last "Saved" or error. */}
      <form onSubmit={handleSubmit} onChange={() => save.reset()} aria-label="Project details">
        <CardContent className="flex flex-col gap-4">
          <FormField id="project-name" label="Name">
            <Input id="project-name" name="name" defaultValue={project.name} required maxLength={200} />
          </FormField>
          <FormField id="project-description" label="Description">
            <Textarea
              id="project-description"
              name="description"
              defaultValue={project.description}
              maxLength={5000}
            />
          </FormField>
          {save.error && (
            <p role="alert" className="text-xs text-destructive">
              {errorMessage(save.error)}
            </p>
          )}
        </CardContent>
        <CardFooter className="mt-4 gap-3">
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? 'Saving…' : 'Save'}
          </Button>
          <p role="status" className="text-xs text-muted-foreground">
            {save.isSuccess && 'Saved.'}
          </p>
        </CardFooter>
      </form>
    </Card>
  )
}
