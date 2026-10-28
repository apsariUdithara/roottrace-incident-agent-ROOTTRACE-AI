// A project's repository, shared by the project settings and the Knowledge page. While it's
// being indexed the query polls, so every page showing the status follows the run.
import { useQuery } from '@tanstack/react-query'

import { api, ok } from '@/api/client'
import type { Schemas } from '@/api/types'

export type Repository = Schemas['RepositoryOut']

/** How often to check on an indexing run. */
export const POLL_MS = 2000

export const repositoriesKey = (projectId: string) => ['projects', projectId, 'repositories']

export function isIndexing(repository: Repository | undefined): boolean {
  return repository?.ingest_status === 'queued' || repository?.ingest_status === 'running'
}

export function useRepositories(projectId: string) {
  return useQuery({
    queryKey: repositoriesKey(projectId),
    queryFn: () =>
      ok(api.GET('/projects/{project_id}/repositories', { params: { path: { project_id: projectId } } })),
    refetchInterval: (query) => (query.state.data?.some(isIndexing) ? POLL_MS : false),
  })
}

/** "acme/payments" for https://github.com/acme/payments */
export function repositoryName(repository: Repository): string {
  return repository.url.replace('https://github.com/', '')
}
