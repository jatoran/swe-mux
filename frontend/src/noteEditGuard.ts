/**
 * What makes an editor commit a *save* rather than an echo.
 *
 * The failure this exists for (2026-08-19 → 2026-08-21): one note held open in two live views
 * wrote itself about once a second for minutes at a time - 1904 saves across the daemon logs -
 * with the stored revision alternating between two values (`0314be…` ↔ `4693eb…` in
 * `daemon.log` at 09:21:52-55). Neither side was editing. Each save produced a `note_changed`
 * the other side followed, and following it re-seeded that side's engine, whose own commit was
 * indistinguishable from typing and went straight back out as a save. The revision CAS cannot
 * break this: both sides rebase onto the revision they were just handed, so every write is
 * legitimate and every write pokes the other. It ended only when a view was closed.
 *
 * Three independent guards, in the order a commit meets them:
 *
 * 1. **Content.** A commit whose canonical form matches what was last loaded or last saved is
 *    not an edit, however different its bytes are. This is what a re-serialization is.
 * 2. **Provenance.** A reload adopts the document; the commit the re-seeded engine emits
 *    afterwards did not come from this human, so it neither dirties the document nor schedules
 *    a save. The latch opens on the next local input and closes again on the next reload.
 * 3. **Rate.** The failsafe for a loop neither of the first two recognises: saves that keep
 *    coming with no local input between them are a machine talking to itself, so autosaving
 *    stops, says so, and reports it. It keys on *absence of input*, never on content or
 *    interval, so genuinely fast typing can never trip it.
 *
 * Guard 2 is the one that ends the observed incident, and it ends it *silently* - so the
 * suppressed echoes are counted on the same threshold as guard 3 and reported once per
 * episode. An incident that leaves no trace is one nobody can attribute next time.
 *
 * Pure and clock-injected: the whole policy is testable without a DOM, a daemon, or a wait.
 */

/** Consecutive input-free saves (or suppressed echoes) that are a loop rather than a burst. */
export const LOOP_SAVE_LIMIT = 6
/** How recent those saves must be to count as one episode. */
export const LOOP_WINDOW_MS = 10_000

const TRAILING_BLANKS = /\n+$/
const TRAILING_SPACE_RUN = /[ \t]+$/

/**
 * The form two serializations of the same document agree on.
 *
 * Only differences that cannot change what the markdown renders are erased, because this
 * decides whether a save fires: line-ending flavour, a byte-order mark, Unicode composition,
 * the number of blank lines a document ends with, and the *width* of a trailing whitespace
 * run. A hard line break (two or more trailing spaces) survives as exactly two spaces - it is
 * content, and collapsing it to none would silently drop a break the user typed - while a
 * single stray trailing space, which renders as nothing, does not.
 */
export function canonicalNoteText(text: string): string {
  const withoutBom = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text
  const lines = withoutBom.replace(/\r\n?/g, '\n').normalize('NFC').split('\n')
  const trimmed = lines.map(line => {
    const run = TRAILING_SPACE_RUN.exec(line)
    if (!run) return line
    const body = line.slice(0, run.index)
    return run[0].startsWith('  ') ? `${body}  ` : body
  })
  return trimmed.join('\n').replace(TRAILING_BLANKS, '')
}

/**
 * Why a commit did or did not become a save. Every value but `save` means no PUT, no dirty
 * document, and no scheduled retry.
 */
export type CommitDecision =
  /** Genuine local work: persist it. */
  | 'save'
  /** Canonically identical to the loaded/saved document: a re-serialization, not an edit. */
  | 'unchanged'
  /** The engine's own commit after a reload, with no local input since: an echo. */
  | 'reloaded'
  /** This commit tripped the rate failsafe and paused autosaving. */
  | 'looping'
  /** Autosaving is already paused, and stays paused until local input or an explicit resume. */
  | 'paused'

/** One episode of machine-driven writing, reported once, durably, by the caller. */
export type LoopReport = {
  /** `paused` stopped autosaving; `echo` was already harmless and is recorded, not acted on. */
  kind: 'paused' | 'echo'
  /** Input-free commits counted in the window that ended the episode. */
  commits: number
  windowMs: number
}

export type GuardReading = {
  paused: boolean
  /** Saves accepted with no local input between them, within the window. */
  inputFreeSaves: number
  /** Post-reload commits suppressed with no local input since, within the window. */
  suppressedEchoes: number
  windowMs: number
}

export type NoteEditGuardOptions = {
  now?: () => number
  saveLimit?: number
  windowMs?: number
}

export class NoteEditGuard {
  private readonly now: () => number
  private readonly saveLimit: number
  private readonly windowMs: number
  /** Canonical form of the document as the daemon last had it from us, or null before a load. */
  private baseline: string | null = null
  /** True between a load and the next local input: commits in that span are the engine's. */
  private awaitingInput = false
  /** Timestamps of accepted saves with no local input between them, oldest first. */
  private inputFree: number[] = []
  /** Timestamps of suppressed post-reload commits, cleared only by real local input. */
  private echoes: number[] = []
  private echoReported = false
  private stopped = false
  private report: LoopReport | null = null

  constructor({
    now = Date.now,
    saveLimit = LOOP_SAVE_LIMIT,
    windowMs = LOOP_WINDOW_MS,
  }: NoteEditGuardOptions = {}) {
    this.now = now
    this.saveLimit = saveLimit
    this.windowMs = windowMs
  }

  /**
   * This text is the document now - a first load, a reload following a remote change, or a
   * conflict resolved from disk. It is never an edit, and neither is the commit the re-seeded
   * engine emits from it, until the human touches this note again.
   *
   * The echo evidence deliberately survives: a reload is exactly what each turn of the loop
   * begins with, so clearing it here would erase the only record the episode leaves.
   */
  adopt(text: string): void {
    this.baseline = canonicalNoteText(text)
    this.awaitingInput = true
    this.inputFree = []
  }

  /**
   * A real, trusted local input reached this note. It opens the reload latch, drops the loop
   * evidence, and releases a pause - a person typing must always be able to save, which is
   * why the failsafe keys on input rather than on content or on rate.
   */
  recordLocalInput(): void {
    this.awaitingInput = false
    this.inputFree = []
    this.echoes = []
    this.echoReported = false
    this.stopped = false
  }

  /** Take the pause off without local input (the user pressed Resume). */
  resume(): void {
    this.stopped = false
    this.inputFree = []
  }

  commit(text: string): CommitDecision {
    if (this.stopped) return 'paused'
    const canonical = canonicalNoteText(text)
    if (this.baseline !== null && canonical === this.baseline) return 'unchanged'
    const at = this.now()
    if (this.awaitingInput) {
      this.echoes = this.recent(this.echoes, at)
      this.echoes.push(at)
      if (this.echoes.length >= this.saveLimit && !this.echoReported) {
        this.echoReported = true
        this.report = { kind: 'echo', commits: this.echoes.length, windowMs: this.windowMs }
      }
      return 'reloaded'
    }
    this.inputFree = this.recent(this.inputFree, at)
    if (this.inputFree.length >= this.saveLimit) {
      this.stopped = true
      this.report = { kind: 'paused', commits: this.inputFree.length, windowMs: this.windowMs }
      return 'looping'
    }
    this.inputFree.push(at)
    this.baseline = canonical
    return 'save'
  }

  /** The pending episode report, at most once per episode. */
  takeReport(): LoopReport | null {
    const report = this.report
    this.report = null
    return report
  }

  reading(): GuardReading {
    const at = this.now()
    return {
      paused: this.stopped,
      inputFreeSaves: this.recent(this.inputFree, at).length,
      suppressedEchoes: this.recent(this.echoes, at).length,
      windowMs: this.windowMs,
    }
  }

  private recent(stamps: number[], at: number): number[] {
    return stamps.filter(stamp => at - stamp < this.windowMs)
  }
}
