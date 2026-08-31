/**
 * Autosave for the Queue pane's inline draft editor.
 *
 * It lives outside the component for the same reason `noteSaveQueue` does: the surface
 * that owns the text is destroyed by the gesture most likely to interrupt someone typing
 * into it. The Queue tab is in the right-edge drawer, and swiping the drawer shut - or
 * moving focus to another session, which retargets the whole pane - unmounts `QueuePane`
 * mid-sentence. A debounce held in component state is cancelled by that unmount, so the
 * last thing typed is the thing lost. Here, the timer and the in-flight request outlive
 * the editor, and `flushAll()` on `pagehide`/`visibilitychange` covers the tab closing
 * before the debounce has even fired.
 *
 * It owns the daemon's optimistic `revision` for each item it is saving. Only two writes
 * exist - create the item on its first non-empty body, then PATCH the body - and both
 * come back with the authoritative revision, so nothing here has to guess one. A
 * `revision_conflict` re-anchors on the revision the daemon reported and retries once,
 * because the only way to hit one is a second surface editing the same item and the
 * newest keystrokes are still the ones the person meant.
 *
 * An empty body is never sent: the daemon refuses one (`invalid_body`), so a draft with
 * nothing in it stays purely local and a saved item that is cleared keeps its last saved
 * text rather than erroring in a loop. That is also what makes "+" cheap - pressing it
 * writes nothing until there is something to write.
 */

// Extension-qualified so the node test runner can load this module directly.
import { enqueueMessage, editQueueMessage, type QueueConstraints, type QueueMessage } from './queueApi.ts'

export type QueueDraftStatus =
  /** Nothing to save: an untouched or still-empty local draft. */
  | 'idle'
  /** Edited; a save is scheduled. */
  | 'pending'
  | 'saving'
  | 'saved'
  /** A persisted item whose body was cleared. The daemon will not take it. */
  | 'empty'
  | 'error'

/** What the editor hands over when it opens on a row. */
export type QueueDraftSeed = {
  sessionId: string
  /** `''` for a draft that has never been persisted. */
  messageId: string
  revision: number
  body: string
  /** Create-time only: mid-turn delivery asked for before the item existed.
   *
   *  Arming is deliberately *not* here. It is a separate write in every case — an item has
   *  to exist before it can be armed — so folding it into the create would give arming two
   *  implementations that differ only in whether the person typed fast. */
  constraints: QueueConstraints | null
}

/** The half of an entry a renderer needs. */
export type QueueDraftState = {
  messageId: string
  revision: number
  status: QueueDraftStatus
  error: string
  /** True while the typed body differs from the one the daemon has acked. */
  dirty: boolean
}

export type QueueDraftTransport = {
  create: (
    sessionId: string,
    body: string,
    options: { constraints?: QueueConstraints },
  ) => Promise<QueueMessage>
  update: (messageId: string, revision: number, body: string) => Promise<QueueMessage>
}

type Entry = QueueDraftSeed & {
  status: QueueDraftStatus
  error: string
  /** The last body the daemon acknowledged; `''` until the first successful write. */
  savedBody: string
  timer: ReturnType<typeof setTimeout> | null
  chain: Promise<void> | null
}

export const QUEUE_DRAFT_DEBOUNCE_MS = 500

/** How many times one save may re-anchor on a conflicting revision before giving up. */
const MAX_CONFLICT_RETRIES = 1

/** How many times `flush` re-runs to catch keystrokes that landed mid-request. */
const MAX_FLUSH_PASSES = 3

const errorText = (cause: unknown): string =>
  cause instanceof Error ? cause.message : String(cause)

/** The revision the daemon says the item is really at, or null when this was not a conflict. */
function conflictRevision(cause: unknown): number | null {
  const detail = (cause as { detail?: Record<string, unknown> } | null)?.detail
  if (!detail || String(detail.code || '') !== 'revision_conflict') return null
  const revision = Number(detail.revision)
  return Number.isFinite(revision) ? revision : null
}

const stateOf = (entry: Entry): QueueDraftState => ({
  messageId: entry.messageId,
  revision: entry.revision,
  status: entry.status,
  error: entry.error,
  dirty: entry.body !== entry.savedBody,
})

export class QueueDraftSaver {
  private entries = new Map<string, Entry>()
  private listeners = new Set<(key: string, state: QueueDraftState) => void>()
  // Written out rather than declared as constructor parameter properties: this module is
  // unit-tested under node's strip-only type runner, which rejects those outright.
  private transport: QueueDraftTransport
  private debounceMs: number

  constructor(transport: QueueDraftTransport, debounceMs: number = QUEUE_DRAFT_DEBOUNCE_MS) {
    this.transport = transport
    this.debounceMs = debounceMs
  }

  subscribe(listener: (key: string, state: QueueDraftState) => void): () => void {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  /** Begin (or re-seed) autosave for one editor key. Re-opening an already-open key keeps
   *  its pending work rather than restarting it, so re-mounting the pane over a live edit
   *  is not a way to drop one. */
  open(key: string, seed: QueueDraftSeed): QueueDraftState {
    const existing = this.entries.get(key)
    if (existing) return stateOf(existing)
    const entry: Entry = {
      ...seed,
      status: 'idle',
      error: '',
      savedBody: seed.messageId ? seed.body : '',
      timer: null,
      chain: null,
    }
    this.entries.set(key, entry)
    return stateOf(entry)
  }

  state(key: string): QueueDraftState | null {
    const entry = this.entries.get(key)
    return entry ? stateOf(entry) : null
  }

  /** Record a change and schedule the write. Body edits debounce; `constraints` is a
   *  create-time fact and is simply remembered until the create happens. */
  edit(
    key: string,
    patch: { body?: string; constraints?: QueueConstraints | null },
  ): void {
    const entry = this.entries.get(key)
    if (!entry) return
    if (patch.constraints !== undefined) entry.constraints = patch.constraints
    if (patch.body === undefined || patch.body === entry.body) return
    entry.body = patch.body
    this.mark(key, entry.body.trim() || entry.messageId ? 'pending' : 'idle', '')
    this.schedule(key)
  }

  /** Write now and wait for it, including anything typed while the request was in flight. */
  async flush(key: string): Promise<QueueDraftState | null> {
    for (let pass = 0; pass < MAX_FLUSH_PASSES; pass += 1) {
      const entry = this.entries.get(key)
      if (!entry) return null
      if (entry.timer) { clearTimeout(entry.timer); entry.timer = null }
      await this.save(key)
      const after = this.entries.get(key)
      if (!after) return null
      // Converged, refused (an empty body is not retried), or failed: stop.
      if (after.body === after.savedBody || !after.body.trim() || after.status === 'error') {
        return stateOf(after)
      }
    }
    const final = this.entries.get(key)
    return final ? stateOf(final) : null
  }

  /** Save every open editor. The unmount and page-teardown path. */
  async flushAll(): Promise<void> {
    await Promise.all([...this.entries.keys()].map(key => this.flush(key)))
  }

  /** Stop tracking a key. It does not save - callers `flush` first when they mean to. */
  close(key: string): void {
    const entry = this.entries.get(key)
    if (!entry) return
    if (entry.timer) clearTimeout(entry.timer)
    this.entries.delete(key)
  }

  private mark(key: string, status: QueueDraftStatus, error: string): void {
    const entry = this.entries.get(key)
    if (!entry) return
    entry.status = status
    entry.error = error
    const snapshot = stateOf(entry)
    for (const listener of this.listeners) listener(key, snapshot)
  }

  private schedule(key: string): void {
    const entry = this.entries.get(key)
    if (!entry) return
    if (entry.timer) clearTimeout(entry.timer)
    entry.timer = setTimeout(() => {
      const live = this.entries.get(key)
      if (live) live.timer = null
      void this.save(key)
    }, this.debounceMs)
  }

  /** Serialize writes per key: two PATCHes racing on one item is a self-inflicted
   *  revision conflict, and the loser would be the newer text. */
  private async save(key: string): Promise<void> {
    const entry = this.entries.get(key)
    if (!entry) return
    const run = (entry.chain ?? Promise.resolve()).then(() => this.saveOnce(key, 0))
    entry.chain = run.catch(() => {})
    await entry.chain
  }

  private async saveOnce(key: string, retries: number): Promise<void> {
    const entry = this.entries.get(key)
    if (!entry) return
    const body = entry.body
    if (!body.trim()) {
      // Never sent. An unsaved draft is simply still local; a saved item keeps the last
      // body the daemon took, because it refuses an empty one outright.
      this.mark(
        key,
        entry.messageId ? 'empty' : 'idle',
        entry.messageId ? 'A queued message cannot be empty — the last saved text is kept.' : '',
      )
      return
    }
    if (entry.messageId && body === entry.savedBody) {
      this.mark(key, 'saved', '')
      return
    }
    this.mark(key, 'saving', '')
    try {
      const saved = entry.messageId
        ? await this.transport.update(entry.messageId, entry.revision, body)
        : await this.transport.create(entry.sessionId, body, {
          constraints: entry.constraints ?? undefined,
        })
      const live = this.entries.get(key)
      if (!live) return
      live.messageId = saved.id
      live.revision = saved.revision
      live.savedBody = body
      this.mark(key, 'saved', '')
      // Keystrokes that landed while the request was open: the debounce timer may
      // already have fired for them, so nothing else would pick them up.
      if (live.body !== live.savedBody) this.schedule(key)
    } catch (cause) {
      const live = this.entries.get(key)
      if (!live) return
      const revision = conflictRevision(cause)
      if (revision !== null && retries < MAX_CONFLICT_RETRIES) {
        live.revision = revision
        await this.saveOnce(key, retries + 1)
        return
      }
      this.mark(key, 'error', errorText(cause))
    }
  }
}

export const queueDraftSaver = new QueueDraftSaver({
  create: (sessionId, body, options) => enqueueMessage(sessionId, body, options),
  update: (messageId, revision, body) => editQueueMessage(messageId, revision, body),
})

// The tab going away is the one teardown no unmount handler sees.
if (typeof window !== 'undefined') {
  const flush = () => { void queueDraftSaver.flushAll() }
  window.addEventListener('pagehide', flush)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush()
  })
}
