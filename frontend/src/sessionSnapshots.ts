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
 * presentation enrichment for the same run. Fields an event patched in place at a
 * revision newer than the incoming snapshot keep their patched values.
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
    // Nor can it say whether it was read before or after an event patch this row
    // already holds, so the patches stand until a versioned snapshot passes them.
    return retainNewerPatches(current, merged, null)
  }
  // A new daemon generation is a new revision domain: nothing patched under the old
  // one can be ordered against it, and the snapshot is the whole truth.
  if (!comparable) return withoutPatches(merged)
  return retainNewerPatches(current, merged, Number(incomingRevision))
}

/**
 * One event's in-place update to a session row: the complete new values of the
 * fields the change wrote, and the session revision `publish_update` stamped when
 * it made them (`src/swe_mux/session_patch.py`).
 */
export type SessionRowPatch = {
  fields: Record<string, unknown>
  revision: number
}

/**
 * Fields a patch may never write. Identity and Project membership are structural -
 * a change to either is answered by re-reading the fleet, not by editing one row -
 * and the underscore fields are this client's own ordering bookkeeping.
 */
function patchableField(field: string): boolean {
  return field !== 'id' && field !== 'project_id' && !field.startsWith('_')
}

/**
 * Apply an event's row patch, ordered against the snapshots the row came from.
 *
 * The patch is in the same revision domain as the snapshots only when the row was
 * read from the daemon generation that sent the event; otherwise this returns
 * `null`, and the caller re-reads the row instead of guessing. A patch at or below
 * the row's revision is already contained in it and changes nothing. A newer one
 * writes its fields and records their revision in `_field_revisions` without
 * advancing `_snapshot_revision`, because every other field of the row is still as
 * old as that snapshot - so a snapshot taken between the two still merges, and
 * keeps the patched fields (`retainNewerPatches`).
 */
export function applySessionRowPatch(
  current: Session, patch: SessionRowPatch, generation: string | null,
): Session | null {
  const rowRevision = current._snapshot_revision
  if (!generation || current._snapshot_generation !== generation || !Number.isFinite(rowRevision)) {
    return null
  }
  if (patch.revision <= Number(rowRevision)) return current
  const revisions = { ...(current._field_revisions ?? {}) }
  let next: Session | null = null
  for (const [field, value] of Object.entries(patch.fields)) {
    if (!patchableField(field)) continue
    // Two patches to one field can arrive out of order only across reconnects, but
    // the older must still never overwrite the newer.
    if ((revisions[field] ?? Number.NEGATIVE_INFINITY) >= patch.revision) continue
    next = next ?? { ...current }
    fieldsOf(next)[field] = value
    revisions[field] = patch.revision
  }
  if (!next) return current
  next._field_revisions = revisions
  return next
}

/** A row as the field map a patch addresses by name. */
function fieldsOf(session: Session): Record<string, unknown> {
  return session as unknown as Record<string, unknown>
}

/** Carry forward the row's patched fields newer than the snapshot replacing it. */
function retainNewerPatches(current: Session, merged: Session, incomingRevision: number | null): Session {
  const revisions = current._field_revisions
  if (!revisions) return withoutPatches(merged)
  const kept = Object.entries(revisions)
    .filter(([, revision]) => incomingRevision === null || revision > incomingRevision)
  if (kept.length === 0) return withoutPatches(merged)
  // A copy, never the incoming snapshot itself: the fleet refresh hands the same
  // objects to layout planning, which must see what the daemon said.
  const next = { ...merged, _field_revisions: Object.fromEntries(kept) }
  for (const [field] of kept) fieldsOf(next)[field] = fieldsOf(current)[field]
  return next
}

function withoutPatches(session: Session): Session {
  if (session._field_revisions === undefined) return session
  const next = { ...session }
  delete next._field_revisions
  return next
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
