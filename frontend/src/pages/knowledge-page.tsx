// /knowledge: what the agent searches in the current project, and (re-)indexing its repository.
// The tab, search and page live in the URL (?type=code&q=pool&offset=50), so a view can be shared.
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'

import { api, ok } from '@/api/client'
import type { Schemas } from '@/api/types'
import { IngestBadge } from '@/components/ingest-status'
import { MemoriesCard } from '@/components/memories-card'
import { PageHeader } from '@/components/page-header'
import { SOURCE_ICONS } from '@/components/source-chip'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useCurrentProject } from '@/lib/current-project'
import { errorMessage } from '@/lib/errors'
import {
  isIndexing,
  repositoriesKey,
  repositoryName,
  useRepositories,
  type Repository,
} from '@/lib/repositories'
import { useDebouncedParam, useSearchParamsUpdate } from '@/lib/search-params'
import { useSourceViewer } from '@/lib/source-viewer'

type SourceType = Schemas['DocumentSourceType']
type Item = Schemas['KnowledgeItem']

const TABS: { type: SourceType; label: string; empty: string }[] = [
  { type: 'doc', label: 'Docs', empty: 'No docs indexed.' },
  { type: 'code', label: 'Code', empty: 'No code indexed.' },
  { type: 'issue', label: 'Issues', empty: 'No issues indexed.' },
  { type: 'incident', label: 'Incidents', empty: 'No past incidents yet.' },
]
const PAGE_SIZE = 50

export function KnowledgePage() {
  const { project, isPending, isError } = useCurrentProject()

  return (
    <div className="flex max-w-4xl flex-col gap-6">
      <PageHeader
        title="Knowledge"
        description="What the agent searches in this project: the repository's docs and code, issues, past incidents, and what it remembers."
      />
      {isPending && <Skeleton className="h-24" />}
      {isError && (
        <p role="alert" className="text-xs text-destructive">
          Couldn't load your projects. Reload the page to try again.
        </p>
      )}
      {!isPending && !isError && !project && (
        <p className="text-sm text-muted-foreground">
          <Link to="/projects" className="text-primary hover:underline">
            Create a project
          </Link>{' '}
          first, then connect its repository.
        </p>
      )}
      {project && <ProjectKnowledge key={project.id} projectId={project.id} />}
    </div>
  )
}

function ProjectKnowledge({ projectId }: { projectId: string }) {
  const repositories = useRepositories(projectId)
  const repository = repositories.data?.[0]

  return (
    <>
      {repositories.isPending && <Skeleton className="h-24" />}
      {repositories.isError && (
        <p role="alert" className="text-xs text-destructive">
          Couldn't load the repository. Reload the page to try again.
        </p>
      )}
      {repositories.data && !repository && (
        <Card>
          <CardHeader>
            <CardTitle>No repository yet</CardTitle>
            <CardDescription>
              <Link to={`/projects/${projectId}`} className="text-primary hover:underline">
                Connect a GitHub repository
              </Link>{' '}
              to this project, then index it here.
            </CardDescription>
          </CardHeader>
        </Card>
      )}
      {repository && <IndexingCard projectId={projectId} repository={repository} />}
      {repositories.data && <KnowledgeList projectId={projectId} repository={repository} />}
      <MemoriesCard projectId={projectId} />
    </>
  )
}

/** The repository's indexing status, and the button that starts a run. */
function IndexingCard({ projectId, repository }: { projectId: string; repository: Repository }) {
  const queryClient = useQueryClient()
  const ingest = useMutation({
    mutationFn: () =>
      ok(api.POST('/projects/{project_id}/ingest', { params: { path: { project_id: projectId } } })),
    // The answer has the repository as queued: showing it starts the polling.
    onSuccess: (repositories) => queryClient.setQueryData(repositoriesKey(projectId), repositories),
  })
  const running = isIndexing(repository)

  return (
    <Card>
      <CardHeader>
        <CardTitle>{repositoryName(repository)}</CardTitle>
        <CardDescription>Indexed from {repository.default_branch}</CardDescription>
        <CardAction>
          <Button disabled={running || ingest.isPending} onClick={() => ingest.mutate()}>
            {repository.last_indexed_at ? 'Re-index' : 'Index now'}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col items-start gap-1">
        <div role="status" className="flex flex-col items-start gap-1">
          <IngestBadge status={repository.ingest_status} />
          <IndexingDetail repository={repository} />
        </div>
        {ingest.error && (
          <p role="alert" className="text-xs text-destructive">
            {errorMessage(ingest.error)}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function IndexingDetail({ repository }: { repository: Repository }) {
  const last = repository.last_indexed_sha && (
    <>
      commit <code>{repository.last_indexed_sha.slice(0, 7)}</code>
      {repository.last_indexed_at && `, ${formatTime(repository.last_indexed_at)}`}
    </>
  )
  switch (repository.ingest_status) {
    case 'queued':
    case 'running':
      return (
        <p className="text-xs text-muted-foreground">
          Reading the repository and embedding what changed. This page follows along.
        </p>
      )
    case 'failed':
      return (
        <>
          <p className="text-xs text-destructive">{repository.ingest_error}</p>
          {last && <p className="text-xs text-muted-foreground">The agent still searches {last}.</p>}
        </>
      )
    case 'indexed':
      return <p className="text-xs text-muted-foreground">At {last}.</p>
    case 'not_indexed':
      return (
        <p className="text-xs text-muted-foreground">
          Index it so the agent can search its docs and code.
        </p>
      )
  }
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

function KnowledgeList({ projectId, repository }: { projectId: string; repository?: Repository }) {
  const [params] = useSearchParams()
  const type = TABS.find((tab) => tab.type === params.get('type'))?.type ?? 'doc'
  const q = params.get('q') ?? ''
  const offset = Math.max(0, Number(params.get('offset')) || 0)
  const [draft, setDraft] = useDebouncedParam('q') // searches once typing pauses
  const update = useSearchParamsUpdate()
  const openSource = useSourceViewer()

  const items = useQuery({
    // A finished run changes last_indexed_at, and so the key: the list then reloads by itself.
    queryKey: [
      'projects',
      projectId,
      'knowledge',
      { type, q, offset, indexedAt: repository?.last_indexed_at },
    ],
    queryFn: () =>
      ok(
        api.GET('/projects/{project_id}/knowledge', {
          params: {
            path: { project_id: projectId },
            query: { type, q: q || undefined, limit: PAGE_SIZE, offset },
          },
        }),
      ),
    placeholderData: keepPreviousData, // the old page stays up while the next one loads
  })

  return (
    <Tabs value={type} onValueChange={(value) => update({ type: value as SourceType, offset: null })}>
      <div className="flex flex-wrap items-center gap-3">
        <TabsList>
          {TABS.map((tab) => {
            const Icon = SOURCE_ICONS[tab.type]
            return (
              <TabsTrigger key={tab.type} value={tab.type}>
                <Icon data-icon="inline-start" />
                {tab.label}
              </TabsTrigger>
            )
          })}
        </TabsList>
        <Input
          type="search"
          aria-label="Search titles and paths"
          placeholder="Search titles and paths"
          value={draft}
          maxLength={200}
          onChange={(event) => setDraft(event.target.value)}
          className="w-64"
        />
        {type === 'incident' && (
          <Link to="/upload?kind=incidents" className="ml-auto text-xs text-primary hover:underline">
            Upload incident history
          </Link>
        )}
      </div>
      <TabsContent value={type} className="flex flex-col gap-3">
        {items.isPending && <Skeleton className="h-40" />}
        {items.isError && (
          <p role="alert" className="text-xs text-destructive">
            {errorMessage(items.error)}
          </p>
        )}
        {items.data && (
          <ItemList
            page={items.data}
            onOpen={(item) => openSource(projectId, item.source_ref)}
            empty={emptyMessage(type, q, repository)}
            onPage={(next) => update({ offset: next })}
          />
        )}
      </TabsContent>
    </Tabs>
  )
}

function emptyMessage(type: SourceType, q: string, repository?: Repository): string {
  if (q) return `Nothing matches “${q}”.`
  const fromRepository = type === 'doc' || type === 'code'
  if (fromRepository && !repository?.last_indexed_at) {
    return repository ? 'Nothing indexed yet. Index the repository above.' : 'Nothing indexed yet.'
  }
  return TABS.find((tab) => tab.type === type)!.empty
}

function ItemList({
  page,
  empty,
  onOpen,
  onPage,
}: {
  page: Schemas['KnowledgePage']
  empty: string
  onOpen: (item: Item) => void
  onPage: (offset: number) => void
}) {
  if (page.total === 0) return <p className="py-6 text-sm text-muted-foreground">{empty}</p>
  const first = page.offset + 1
  const last = page.offset + page.items.length

  return (
    <>
      <ul aria-label="Indexed items" className="divide-y rounded-lg ring-1 ring-foreground/10">
        {page.items.map((item) => (
          <ItemRow key={item.id} item={item} onOpen={() => onOpen(item)} />
        ))}
      </ul>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span>
          {first}–{last} of {page.total}
        </span>
        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          disabled={page.offset === 0}
          onClick={() => onPage(Math.max(0, page.offset - page.limit))}
        >
          Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={last >= page.total}
          onClick={() => onPage(page.offset + page.limit)}
        >
          Next
        </Button>
      </div>
    </>
  )
}

/** One indexed item; it opens in the source viewer. */
function ItemRow({ item, onOpen }: { item: Item; onOpen: () => void }) {
  const Icon = SOURCE_ICONS[item.source_type]
  // A file's title is just its name: show the path instead. Issues and incidents have real titles.
  const title = item.title && !item.path.endsWith(item.title) ? item.title : item.path
  const chunks = `${item.chunk_count} ${item.chunk_count === 1 ? 'chunk' : 'chunks'}`

  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="flex w-full items-center gap-3 px-3 py-2 text-left text-xs hover:bg-muted/50"
      >
        <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="truncate font-medium">{title}</span>
          {title !== item.path && (
            <span className="truncate font-mono text-muted-foreground">{item.path}</span>
          )}
        </div>
        <span className="shrink-0 text-muted-foreground">{chunks}</span>
        {item.commit_sha && (
          <code className="shrink-0 text-muted-foreground">{item.commit_sha.slice(0, 7)}</code>
        )}
      </button>
    </li>
  )
}
