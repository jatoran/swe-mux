/**
 * One shared, self-refreshing copy of a Project's `.swe-mux/config.toml`.
 *
 * The Projects registry used to hold three of these: the defaults form, the
 * automation opt-ins and the agent authority table each read the file for
 * themselves and each cached its own revision. They all write that one file, so the
 * first successful edit anywhere in the panel silently invalidated the other two,
 * and the second edit answered "project config changed externally; reload before
 * saving" until the drawer was closed and reopened. One copy, and every write
 * publishing back into it, is what makes the panel internally consistent.
 *
 * The daemon already announces every write to this file (`project_configuration_changed`,
 * re-broadcast by `App` as `mux:project-automations-changed`), which is how an edit
 * made by a grant gate, the land queue, the configurator agent or another device
 * reaches an open panel. Subscribing here is safe because no section keeps its draft
 * in this object: the one section that drafts at all overlays its edits on top, so a
 * refresh moves the fields nobody is editing and leaves the rest alone.
 */

import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { PROJECT_AUTOMATIONS_CHANGED } from './projectAutomations'
import {
  conflictNotice,
  projectConfigBase,
  revisionConflict,
  type ProjectConfig,
  type ProjectConfigChanges,
} from './projectConfig'

export type ProjectConfigStore = {
  /** The file as the daemon last reported it, or `null` while unread or unreadable. */
  config: ProjectConfig | null
  /** Why the last read failed, for a panel that has nowhere else to say so. */
  error: string
  /** Re-read the file. Never rejects; a failure lands in `error`. */
  refresh: () => Promise<void>
  /**
   * Write only the named fields, `null` to remove one, and publish the result.
   *
   * Rejects on failure. A field-scoped conflict resyncs this copy *before*
   * rejecting, so the message can say what moved and the operator's next click acts
   * on the current file instead of failing the same way again.
   */
  commit: (changes: ProjectConfigChanges) => Promise<ProjectConfig>
}

type ConfigProject = { id: string; root: string; root_available?: boolean }

export function useProjectConfig(project: ConfigProject | null): ProjectConfigStore {
  const projectId = project?.id || ''
  const projectRoot = project?.root || ''
  const readable = !!project && project.root_available !== false
  const [config, setConfig] = useState<ProjectConfig | null>(null)
  const [error, setError] = useState('')
  // `commit` composes its `base` from the copy in hand, so it reads through a ref
  // rather than a closure that a render in flight may have already staled.
  const held = useRef<ProjectConfig | null>(null)
  const issued = useRef(0)
  const painted = useRef(0)

  // Reads and writes race: a refresh fired by the daemon's broadcast can answer after
  // the write that caused it. Only the newest request may paint, so the panel never
  // steps backwards onto a copy that a later answer already replaced.
  const adopt = useCallback((next: ProjectConfig | null, ticket: number) => {
    if (ticket < painted.current) return
    painted.current = ticket
    held.current = next
    setConfig(next)
  }, [])

  const refresh = useCallback(async () => {
    if (!projectId || !projectRoot || !readable) {
      adopt(null, ++issued.current)
      return
    }
    const ticket = ++issued.current
    try {
      // project_id names the registered Project explicitly: without it the daemon
      // re-resolves the root through Git, and a Project registered inside a larger
      // worktree edits the enclosing worktree's config instead of its own.
      const result = await api<ProjectConfig>(
        'GET',
        `/api/project/config?cwd=${encodeURIComponent(projectRoot)}`
        + `&project_id=${encodeURIComponent(projectId)}`,
      )
      adopt(result, ticket)
      if (ticket >= painted.current) setError('')
    } catch (cause) {
      if (ticket < painted.current) return
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }, [projectId, projectRoot, readable, adopt])

  const commit = useCallback(async (changes: ProjectConfigChanges) => {
    const current = held.current
    if (!current) throw new Error('This Project’s .swe-mux/config.toml has not been read yet.')
    const ticket = ++issued.current
    // `undefined` is not a JSON value, so a removal expressed that way would
    // serialize to nothing at all and read as "change no fields".
    const payload = Object.fromEntries(
      Object.entries(changes).map(([key, value]) => [key, value === undefined ? null : value]),
    )
    try {
      const next = await api<ProjectConfig>('PUT', '/api/project/config', {
        cwd: projectRoot,
        project_id: projectId,
        changes: payload,
        base: projectConfigBase(payload, current.values),
      })
      adopt(next, ticket)
      setError('')
      return next
    } catch (cause) {
      const conflict = revisionConflict(cause)
      if (conflict?.current) adopt(conflict.current, ticket)
      else await refresh()
      throw conflict ? new Error(conflictNotice(conflict.fields)) : cause
    }
  }, [projectId, projectRoot, adopt, refresh])

  // Switching Projects clears first and reads second, and the clear takes a ticket of
  // its own so a read still in flight for the previous Project cannot paint over it.
  useEffect(() => {
    adopt(null, ++issued.current)
    setError('')
    void refresh()
  }, [adopt, refresh])

  useEffect(() => {
    if (!projectId) return
    const changed = (event: Event) => {
      const detail = (event as CustomEvent<{ projectId?: string }>).detail
      // An empty id is "some Project" from a daemon that did not say which; re-reading
      // one file is cheaper than being wrong about whether it was this one.
      if (detail?.projectId && detail.projectId !== projectId) return
      void refresh()
    }
    window.addEventListener(PROJECT_AUTOMATIONS_CHANGED, changed)
    return () => window.removeEventListener(PROJECT_AUTOMATIONS_CHANGED, changed)
  }, [projectId, refresh])

  return { config, error, refresh, commit }
}
