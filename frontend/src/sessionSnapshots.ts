import type { Session } from './types'

const presentationKeys = [
  'generated_title',
  'generated_title_annotation',
  'delivery_readiness',
] as const

function runIdentity(session: Session): string {
  return session.agent_run_id || session.id
}

function preservePresentation(base: Session, incoming: Session): Session {
  if (runIdentity(base) !== runIdentity(incoming) || incoming._snapshot_enriched === true) {
    return incoming
  }
  const next = { ...incoming }
  for (const key of presentationKeys) {
    if (next[key] === undefined && base[key] !== undefined) {
      ;(next as Record<string, unknown>)[key] = base[key]
    }
  }
  return next
}

function enrichedPresentation(base: Session, incoming: Session): Session {
  if (runIdentity(base) !== runIdentity(incoming) || incoming._snapshot_enriched !== true) {
    return base
  }
  const next = { ...base }
  for (const key of presentationKeys) {
    if (incoming[key] === undefined) delete (next as Record<string, unknown>)[key]
    else (next as Record<string, unknown>)[key] = incoming[key]
  }
  return next
}

/**
 * Merge one session snapshot from REST, PTY, or a scoped mutation response.
 *
 * REST and PTY are concurrent authorities over the same core record. Every
 * versioned update is ordered by daemon generation and session revision. A
 * stale enriched REST response may still contribute its generated-title fields,
 * but it can never roll core state backward. Raw PTY snapshots never erase
 * presentation enrichment for the same run.
 */
export function mergeSessionSnapshot(current: Session | undefined, incoming: Session): Session {
  if (!current) return incoming

  const currentGeneration = current._snapshot_generation
  const incomingGeneration = incoming._snapshot_generation
  const currentRevision = current._snapshot_revision
  const incomingRevision = incoming._snapshot_revision
  const comparable = !!currentGeneration && currentGeneration === incomingGeneration
    && Number.isFinite(currentRevision) && Number.isFinite(incomingRevision)

  if (comparable && Number(incomingRevision) < Number(currentRevision)) {
    return enrichedPresentation(current, incoming)
  }

  const merged = preservePresentation(current, incoming)
  if (!incomingGeneration && currentGeneration) {
    // Scoped mutation responses predate the snapshot-ordering envelope. They
    // are accepted, but retain the latest ordering watermark so a delayed PTY
    // frame cannot make the object permanently unversioned.
    merged._snapshot_generation = currentGeneration
    merged._snapshot_revision = currentRevision
    merged._snapshot_enriched = current._snapshot_enriched
  }
  return merged
}

/** Replace fleet membership while merging every live row through the ordering contract. */
export function reconcileSessionSnapshots(
  current: Session[], incoming: Session[], optimistic: Session[] = [],
): Session[] {
  const byId = new Map(current.map(session => [session.id, session]))
  return [
    ...incoming.map(session => mergeSessionSnapshot(byId.get(session.id), session)),
    ...optimistic.filter(session => !incoming.some(item => item.id === session.id)),
  ]
}
