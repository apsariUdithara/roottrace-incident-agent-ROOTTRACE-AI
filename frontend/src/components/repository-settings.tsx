// A project's GitHub repository: the connect form, or what's connected with its settings.
// The backend allows one repository per project, because the agent investigates one codebase.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ComponentProps, type FormEvent } from 'react'

import { api, ok } from '@/api/client'
import type { Schemas } from '@/api/types'
import { FormField } from '@/components/form-field'
import { Badge } from '@/components/ui/badge'
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
import { errorMessage } from '@/lib/errors'

type Repository = Schemas['RepositoryOut']
type BadgeVariant = ComponentProps<typeof Badge>['variant']

const SANDBOX: Record<
  Schemas['SandboxStatus'],
  { label: string; detail: string; variant: BadgeVariant }
> = {
  unknown: {
    label: 'Not checked yet',
    detail: 'RootTrace finds out when it first runs the tests.',
    variant: 'outline',
  },
  ready: {
    label: 'Tests run',
    detail: 'A fix is checked by running the tests in the sandbox.',
    variant: 'secondary',
  },
  untestable: {
    label: "Tests can't run",
    detail:
      "Investigations still find the cause, but can't check a fix by running the tests. Check the setup and test commands.",
    variant: 'destructive',
  },
}

const INGEST: Record<Schemas['IngestStatus'], { label: string; variant: BadgeVariant }> = {
  not_indexed: { label: 'Not indexed yet', variant: 'outline' },
  queued: { label: 'Waiting to index', variant: 'outline' },
  running: { label: 'Indexing…', variant: 'secondary' },
  indexed: { label: 'Indexed', variant: 'secondary' },
  failed: { label: 'Indexing failed', variant: 'destructive' },
}

const repositoriesKey = (projectId: string) => ['projects', projectId, 'repositories']

/** Mutations answer with the repository as it now is: put that in the cache. */
function useSetRepositories(projectId: string) {
  const queryClient = useQueryClient()
  return (repositories: Repository[]) =>
    queryClient.setQueryData(repositoriesKey(projectId), repositories)
}

export function RepositorySettings({ projectId }: { projectId: string }) {
  const repositories = useQuery({
    queryKey: repositoriesKey(projectId),
    queryFn: () =>
      ok(api.GET('/projects/{project_id}/repositories', { params: { path: { project_id: projectId } } })),
  })
  const repository = repositories.data?.[0]

  return (
    <Card>
      <CardHeader>
        <CardTitle>Repository</CardTitle>
        <CardDescription>
          The GitHub repository RootTrace reads, and whose tests it runs to check a fix.
        </CardDescription>
      </CardHeader>
      {repositories.isPending && (
        <CardContent>
          <Skeleton className="h-24" />
        </CardContent>
      )}
      {repositories.isError && (
        <CardContent>
          <p role="alert" className="text-xs text-destructive">
            Couldn't load the repository. Reload the page to try again.
          </p>
        </CardContent>
      )}
      {repositories.data &&
        (repository ? (
          <>
            <RepositoryFacts repository={repository} />
            <EditRepositoryForm key={repository.id} projectId={projectId} repository={repository} />
            <DisconnectRepository projectId={projectId} repository={repository} />
          </>
        ) : (
          <ConnectRepositoryForm projectId={projectId} />
        ))}
    </Card>
  )
}

/** The value of a text field, trimmed. */
function field(form: FormData, name: string): string {
  return String(form.get(name) ?? '').trim()
}

function ConnectRepositoryForm({ projectId }: { projectId: string }) {
  const setRepositories = useSetRepositories(projectId)
  const connect = useMutation({
    mutationFn: (body: Schemas['RepositoryCreate']) =>
      ok(
        api.POST('/projects/{project_id}/repositories', {
          params: { path: { project_id: projectId } },
          body,
        }),
      ),
    onSuccess: (repository) => setRepositories([repository]),
  })

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    connect.mutate({
      provider: 'github',
      url: field(form, 'url'),
      default_branch: field(form, 'default_branch'),
      token: field(form, 'token') || null,
      setup_command: field(form, 'setup_command') || null, // empty: auto-detect
      test_command: field(form, 'test_command'),
    })
  }

  return (
    <form onSubmit={handleSubmit} aria-label="Connect a repository">
      <CardContent className="flex flex-col gap-4">
        <FormField id="repository-url" label="GitHub repository" hint="For example https://github.com/acme/payments">
          <Input
            id="repository-url"
            name="url"
            type="url"
            required
            maxLength={500}
            placeholder="https://github.com/owner/repository"
            pattern="https://github\.com/[\w.\-]+/[\w.\-]+"
            aria-describedby="repository-url-hint"
          />
        </FormField>
        <TokenField
          label="Access token (optional)"
          hint="Needed for a private repository, and to open draft PRs. It's stored encrypted and can't be shown again."
        />
        <CommandFields />
        {connect.error && (
          <p role="alert" className="text-xs text-destructive">
            {errorMessage(connect.error)}
          </p>
        )}
      </CardContent>
      <CardFooter className="mt-4">
        <Button type="submit" disabled={connect.isPending}>
          {connect.isPending ? 'Connecting…' : 'Connect repository'}
        </Button>
      </CardFooter>
    </form>
  )
}

/** A password field, so the token isn't shown on screen. Never filled in from what's saved: the
 * backend never sends the token back. */
function TokenField({
  label,
  hint,
  value,
  onChange,
}: {
  label: string
  hint: string
  value?: string
  onChange?: (value: string) => void
}) {
  return (
    <FormField id="repository-token" label={label} hint={hint}>
      <Input
        id="repository-token"
        name="token"
        type="password"
        autoComplete="off"
        spellCheck={false}
        maxLength={255}
        value={value}
        onChange={onChange && ((event) => onChange(event.target.value))}
        aria-describedby="repository-token-hint"
      />
    </FormField>
  )
}

function CommandFields({ repository }: { repository?: Repository }) {
  return (
    <>
      <FormField id="repository-branch" label="Default branch">
        <Input
          id="repository-branch"
          name="default_branch"
          required
          maxLength={255}
          defaultValue={repository?.default_branch ?? 'main'}
        />
      </FormField>
      <FormField
        id="repository-setup"
        label="Setup command (optional)"
        hint="Installs the dependencies in the sandbox. Leave it empty to use requirements.txt or pyproject.toml."
      >
        <Input
          id="repository-setup"
          name="setup_command"
          maxLength={2000}
          spellCheck={false}
          placeholder="Auto-detect"
          defaultValue={repository?.setup_command ?? ''}
          aria-describedby="repository-setup-hint"
          className="font-mono"
        />
      </FormField>
      <FormField id="repository-tests" label="Test command">
        <Input
          id="repository-tests"
          name="test_command"
          required
          maxLength={2000}
          spellCheck={false}
          defaultValue={repository?.test_command ?? 'pytest -q'}
          className="font-mono"
        />
      </FormField>
    </>
  )
}

function RepositoryFacts({ repository }: { repository: Repository }) {
  const sandbox = SANDBOX[repository.sandbox_status]
  const ingest = INGEST[repository.ingest_status]

  return (
    <CardContent>
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 text-xs">
        <dt className="text-muted-foreground">GitHub</dt>
        <dd>
          <a href={repository.url} target="_blank" rel="noreferrer" className="text-primary hover:underline">
            {repository.url.replace('https://github.com/', '')}
          </a>
        </dd>
        <dt className="text-muted-foreground">Access token</dt>
        <dd>{repository.has_token ? 'Saved (encrypted)' : 'None'}</dd>
        <dt className="text-muted-foreground">Tests</dt>
        <dd className="flex flex-col items-start gap-1">
          <Badge variant={sandbox.variant}>{sandbox.label}</Badge>
          <span className="text-muted-foreground">{sandbox.detail}</span>
        </dd>
        <dt className="text-muted-foreground">Knowledge</dt>
        <dd className="flex flex-col items-start gap-1">
          <Badge variant={ingest.variant}>{ingest.label}</Badge>
          {repository.ingest_error && <span className="text-destructive">{repository.ingest_error}</span>}
          {repository.last_indexed_sha && (
            <span className="text-muted-foreground">
              At commit <code>{repository.last_indexed_sha.slice(0, 7)}</code>
            </span>
          )}
        </dd>
      </dl>
    </CardContent>
  )
}

function EditRepositoryForm({ projectId, repository }: { projectId: string; repository: Repository }) {
  const setRepositories = useSetRepositories(projectId)
  // Kept in state only so it can be emptied once saved.
  const [token, setToken] = useState('')
  const save = useMutation({
    mutationFn: (body: Schemas['RepositoryUpdate']) =>
      ok(
        api.PATCH('/projects/{project_id}/repositories/{repository_id}', {
          params: { path: { project_id: projectId, repository_id: repository.id } },
          body,
        }),
      ),
    onSuccess: (saved) => {
      setRepositories([saved])
      setToken('')
    },
  })

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    save.mutate({
      default_branch: field(form, 'default_branch'),
      setup_command: field(form, 'setup_command') || null, // empty: auto-detect
      test_command: field(form, 'test_command'),
      ...(token.trim() && { token: token.trim() }), // not sent: the saved token stays
    })
  }

  return (
    <form
      onSubmit={handleSubmit}
      onChange={() => save.reset()} // any edit clears the last "Saved" or error
      aria-label="Repository settings"
      className="mt-2 border-t pt-4"
    >
      <CardContent className="flex flex-col gap-4">
        <CommandFields repository={repository} />
        <TokenField
          label={repository.has_token ? 'Replace the access token' : 'Add an access token'}
          hint={
            repository.has_token
              ? 'Leave it empty to keep the saved token.'
              : 'Needed for a private repository, and to open draft PRs. Stored encrypted.'
          }
          value={token}
          onChange={setToken}
        />
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
        {repository.has_token && (
          <Button
            type="button"
            variant="ghost"
            disabled={save.isPending}
            onClick={() => save.mutate({ token: null })}
          >
            Remove token
          </Button>
        )}
        <p role="status" className="text-xs text-muted-foreground">
          {save.isSuccess && 'Saved.'}
        </p>
      </CardFooter>
    </form>
  )
}

/** Asks first, because the repository's docs and code leave the knowledge base with it. */
function DisconnectRepository({ projectId, repository }: { projectId: string; repository: Repository }) {
  const setRepositories = useSetRepositories(projectId)
  const [confirming, setConfirming] = useState(false)
  const disconnect = useMutation({
    mutationFn: () =>
      ok(
        api.DELETE('/projects/{project_id}/repositories/{repository_id}', {
          params: { path: { project_id: projectId, repository_id: repository.id } },
        }),
      ),
    onSuccess: () => setRepositories([]),
  })
  const name = repository.url.replace('https://github.com/', '')

  return (
    <CardFooter className="mt-4 flex-col items-start gap-3 border-t">
      {confirming ? (
        <div role="group" aria-label="Disconnect the repository" className="flex flex-col gap-3">
          <p className="text-xs">
            Disconnect {name}? Its docs and code are removed from the knowledge base. Incidents and
            logs stay.
          </p>
          <div className="flex gap-2">
            <Button variant="destructive" disabled={disconnect.isPending} onClick={() => disconnect.mutate()}>
              {disconnect.isPending ? 'Disconnecting…' : 'Disconnect'}
            </Button>
            {/* Takes the focus, since the button that opened this is gone. */}
            <Button variant="ghost" autoFocus onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <Button variant="outline" onClick={() => setConfirming(true)}>
          Disconnect repository
        </Button>
      )}
      {disconnect.error && (
        <p role="alert" className="text-xs text-destructive">
          {errorMessage(disconnect.error)}
        </p>
      )}
    </CardFooter>
  )
}
