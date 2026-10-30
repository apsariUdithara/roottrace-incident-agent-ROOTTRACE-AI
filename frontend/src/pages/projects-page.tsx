import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'

import { api, ok } from '@/api/client'
import type { Schemas } from '@/api/types'
import { FormField } from '@/components/form-field'
import { PageHeader } from '@/components/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { selectProject, useCurrentProject } from '@/lib/current-project'
import { errorMessage } from '@/lib/errors'

type Project = Schemas['ProjectOut']

export function ProjectsPage() {
  const { projects, project: current, isPending, isError } = useCurrentProject()

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <PageHeader
        title="Projects"
        description="A project is one codebase you investigate: its repository, docs, incidents and logs."
      />
      <section aria-label="Your projects" className="flex flex-col gap-3">
        {isPending && <Skeleton className="h-16" />}
        {isError && (
          <p role="alert" className="text-[0.8125rem] text-destructive">
            Couldn't load your projects. Reload the page to try again.
          </p>
        )}
        {!isPending && !isError && projects.length === 0 && (
          <p className="text-sm text-muted-foreground">No projects yet. Create your first one below.</p>
        )}
        {projects.map((project) => (
          <Card key={project.id} size="sm">
            <CardHeader>
              <CardTitle>
                <Link to={`/projects/${project.id}`} className="hover:underline">
                  {project.name}
                </Link>
              </CardTitle>
              <CardDescription>{project.description || 'No description'}</CardDescription>
              {project.id === current?.id && (
                <CardAction>
                  <Badge variant="secondary">Current</Badge>
                </CardAction>
              )}
            </CardHeader>
          </Card>
        ))}
      </section>
      <NewProjectForm />
    </div>
  )
}

/** Creates a project, makes it the current one, and opens its settings to connect a repository. */
function NewProjectForm() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const create = useMutation({
    mutationFn: (body: Schemas['ProjectCreate']) => ok(api.POST('/projects', { body })),
    onSuccess: (created) => {
      queryClient.setQueryData<Project[]>(['projects'], (projects = []) => [created, ...projects])
      selectProject(created.id)
      navigate(`/projects/${created.id}`)
    },
  })

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    create.mutate({ name: String(form.get('name')), description: String(form.get('description')) })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>New project</CardTitle>
      </CardHeader>
      <form onSubmit={handleSubmit} aria-label="New project">
        <CardContent className="flex flex-col gap-4">
          <FormField id="new-project-name" label="Name">
            <Input id="new-project-name" name="name" required maxLength={200} />
          </FormField>
          <FormField id="new-project-description" label="Description (optional)">
            <Textarea id="new-project-description" name="description" maxLength={5000} />
          </FormField>
          {create.error && (
            <p role="alert" className="text-[0.8125rem] text-destructive">
              {errorMessage(create.error)}
            </p>
          )}
        </CardContent>
        <CardFooter className="mt-4">
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? 'Creating…' : 'Create project'}
          </Button>
        </CardFooter>
      </form>
    </Card>
  )
}
