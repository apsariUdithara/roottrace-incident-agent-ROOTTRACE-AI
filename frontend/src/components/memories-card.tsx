// What the agent remembers about a project (long-term memory): shown, corrected, archived or
// deleted here. Agent-written memories are labelled, so nobody trusts them blindly.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, ArchiveRestore, Check, Pencil, Trash2, X } from 'lucide-react'
import { useState } from 'react'

import { api, ok } from '@/api/client'
import type { Schemas } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { SOURCE_TONE } from '@/lib/colors'
import { errorMessage } from '@/lib/errors'

type Memory = Schemas['MemoryOut']

const CATEGORY_TONE: Record<Memory['category'], string> = {
  project_fact: SOURCE_TONE.doc,
  arch_decision: SOURCE_TONE.database,
  previous_incident: SOURCE_TONE.incident,
  investigation_context: SOURCE_TONE.issue,
  user_pref: SOURCE_TONE.memory,
}

// Each action's colour says what it does: archive is reversible (amber), delete is not (red).
const TONE = {
  archive: 'border-amber-500/40 text-amber-700 hover:bg-amber-500/10 hover:text-amber-800 dark:text-amber-300 dark:hover:text-amber-200',
  restore: 'border-emerald-500/40 text-emerald-700 hover:bg-emerald-500/10 hover:text-emerald-800 dark:text-emerald-300 dark:hover:text-emerald-200',
  delete: 'border-red-500/40 text-red-700 hover:bg-red-500/10 hover:text-red-800 dark:text-red-300 dark:hover:text-red-200',
}

export function MemoriesCard({ projectId }: { projectId: string }) {
  const key = ['memories', projectId]
  const memories = useQuery({
    queryKey: key,
    queryFn: () => ok(api.GET('/projects/{project_id}/memories', { params: { path: { project_id: projectId } } })),
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle>Memories</CardTitle>
        <CardDescription>
          What the agent learned from past investigations and recalls in new ones. Archive one to
          stop it being recalled; edit it to correct it.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {memories.isError && <p role="alert" className="text-[0.8125rem] text-destructive">{errorMessage(memories.error)}</p>}
        <ul aria-label="Memories" className="flex flex-col gap-2">
          {memories.data?.map((memory) => (
            <MemoryRow key={memory.id} memory={memory} projectId={projectId} queryKey={key} />
          ))}
        </ul>
        {memories.data?.length === 0 && <p className="text-sm text-muted-foreground">No memories yet.</p>}
      </CardContent>
    </Card>
  )
}

function MemoryRow({ memory, projectId, queryKey }: { memory: Memory; projectId: string; queryKey: string[] }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<string | null>(null)
  const path = { params: { path: { project_id: projectId, memory_id: memory.id } } }
  const refresh = () => queryClient.invalidateQueries({ queryKey })

  const update = useMutation({
    mutationFn: (body: Schemas['MemoryUpdate']) =>
      ok(api.PATCH('/projects/{project_id}/memories/{memory_id}', { ...path, body })),
    onSuccess: () => {
      setEditing(null)
      return refresh()
    },
  })
  const remove = useMutation({
    mutationFn: () => ok(api.DELETE('/projects/{project_id}/memories/{memory_id}', path)),
    onSuccess: refresh,
  })
  const archived = memory.status === 'archived'
  const error = update.error ?? remove.error

  return (
    <li className={`flex flex-col gap-1.5 rounded-md border p-2 text-sm ${archived ? 'opacity-60' : ''}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="outline" className={CATEGORY_TONE[memory.category]}>
          {memory.category.replace(/_/g, ' ')}
        </Badge>
        <Badge variant="outline" className={memory.created_by === 'agent' ? SOURCE_TONE.code : SOURCE_TONE.doc}>
          {memory.created_by === 'agent' ? 'written by the agent' : 'edited by a person'}
        </Badge>
        {archived && <Badge variant="outline">archived</Badge>}
      </div>
      {editing === null ? (
        <p>{memory.content}</p>
      ) : (
        <Textarea aria-label="Memory" value={editing} maxLength={4000} onChange={(e) => setEditing(e.target.value)} />
      )}
      <div className="flex flex-wrap gap-2 pt-1">
        {editing === null ? (
          <>
            <Button size="xs" variant="outline" onClick={() => setEditing(memory.content)}>
              <Pencil data-icon="inline-start" />
              Edit
            </Button>
            <Button
              size="xs"
              variant="outline"
              className={archived ? TONE.restore : TONE.archive}
              disabled={update.isPending}
              onClick={() => update.mutate({ status: archived ? 'active' : 'archived' })}
            >
              {archived ? <ArchiveRestore data-icon="inline-start" /> : <Archive data-icon="inline-start" />}
              {archived ? 'Restore' : 'Archive'}
            </Button>
            <Button
              size="xs"
              variant="outline"
              className={TONE.delete}
              disabled={remove.isPending}
              onClick={() => window.confirm('Delete this memory for good?') && remove.mutate()}
            >
              <Trash2 data-icon="inline-start" />
              Delete
            </Button>
          </>
        ) : (
          <>
            <Button size="xs" disabled={!editing.trim() || update.isPending} onClick={() => update.mutate({ content: editing })}>
              <Check data-icon="inline-start" />
              Save
            </Button>
            <Button size="xs" variant="ghost" onClick={() => setEditing(null)}>
              <X data-icon="inline-start" />
              Cancel
            </Button>
          </>
        )}
      </div>
      {error && <p role="alert" className="text-[0.8125rem] text-destructive">{errorMessage(error)}</p>}
    </li>
  )
}
