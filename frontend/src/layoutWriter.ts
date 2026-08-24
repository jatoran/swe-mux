import { parseLayout, type PaneLayout } from './layout.ts'
import type { Project } from './types.ts'

export type LayoutWriteOptions = {
  /**
   * Suppress the operator-facing message on a rejected write.
   *
   * For writes nobody asked for - the refresh pass that joins a daemon-started session
   * into the tabs - where a lost revision race is two clients converging rather than
   * something to report.
   */
  quiet?: boolean
}

export type LayoutWriterPorts = {
  patch: (projectId: string, layout: PaneLayout, revision: number) => Promise<Project>
  /** Show a layout locally: the optimistic one, then whatever the daemon persisted. */
  showLayout: (projectId: string, layout: PaneLayout) => void
  /** Adopt the Project record the write returned. */
  adoptProject: (project: Project) => void
  /** The revision from the last fleet snapshot, for a Project this writer has not written yet. */
  serverRevision: (projectId: string) => number | undefined
  /** Re-read the fleet after a rejected write, so the operator sees what actually persisted. */
  refresh: () => Promise<void>
  onError: (message: string) => void
}

export type LayoutWriter = {
  /** Write one Project's layout. Resolves true when the daemon accepted it. */
  write(projectId: string, layout: PaneLayout, options?: LayoutWriteOptions): Promise<boolean>
  /** Whether a write for that Project is still in flight. */
  hasPendingWrite(projectId: string): boolean
  /**
   * Adopt each Project's server revision from a fleet snapshot.
   *
   * A Project with a write in flight is skipped, and its layout is skipped by the
   * refresh pass for the same reason: adopting the revision there is what let a second
   * drag base itself on the clobbered layout and then win the write, silently reverting
   * the first move for every client.
   */
  adoptRevisions(projects: readonly Project[]): void
}

/**
 * Project layout is optimistic durable state, and this is the one place that writes it.
 *
 * Three rules live here:
 *
 *  - **One write at a time per Project.** Writes chain rather than race, so two drags in
 *    quick succession reach the daemon in the order the operator made them.
 *  - **A newer optimistic layout wins over an older reply.** Each write takes a
 *    generation; a reply whose generation has been superseded updates the revision and
 *    the Project record but does not snap the layout back to what it persisted.
 *  - **A rejected write re-reads the fleet.** The daemon is authoritative on conflict;
 *    the operator sees the layout that actually exists rather than the one they lost.
 */
export function createLayoutWriter(ports: LayoutWriterPorts): LayoutWriter {
  const revisions: Record<string, number> = {}
  const chains: Record<string, Promise<boolean>> = {}
  const generations: Record<string, number> = {}

  const write = async (projectId: string, layout: PaneLayout, options?: LayoutWriteOptions): Promise<boolean> => {
    ports.showLayout(projectId, layout)
    const generation = (generations[projectId] || 0) + 1
    generations[projectId] = generation
    const previous = chains[projectId] || Promise.resolve(true)
    const operation = previous.catch(() => false).then(async () => {
      const revision = revisions[projectId] ?? ports.serverRevision(projectId) ?? 0
      try {
        const updated = await ports.patch(projectId, layout, revision)
        revisions[projectId] = updated.layout_revision
        ports.adoptProject(updated)
        if (generations[projectId] === generation) ports.showLayout(projectId, parseLayout(updated.layout))
        return true
      } catch (cause) {
        await ports.refresh()
        const message = cause instanceof Error ? cause.message : String(cause)
        if (!options?.quiet) {
          ports.onError(message.includes('stale layout revision')
            ? 'Layout changed in another client; reloaded the current layout.'
            : message)
        }
        return false
      }
    })
    chains[projectId] = operation
    const persisted = await operation
    if (chains[projectId] === operation) delete chains[projectId]
    return persisted
  }

  return {
    write,
    hasPendingWrite: projectId => chains[projectId] !== undefined,
    adoptRevisions: projects => {
      for (const project of projects) {
        if (chains[project.id] !== undefined) continue
        revisions[project.id] = project.layout_revision
      }
    },
  }
}
