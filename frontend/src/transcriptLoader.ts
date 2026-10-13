// The Transcript tab's conversation with the daemon: one request at a time.
//
// Every reason the reader has to look again - a turn ended, a user message landed, the
// agent appended mid-turn, the events socket reconnected, the tab came back to the
// foreground - arrives as a trigger here. Triggers that arrive while a request is in
// flight collapse into one follow-up, so a reconnect that replays forty turn ends
// costs two requests rather than forty, and a response can never land on top of a
// newer one. What each request asks for is decided when it is sent, from the reading
// held at that moment: a delta when the daemon can extend it, the tail when it cannot.
//
// Plain TypeScript with an injected fetch and clock, so the ordering rules are tested
// under the node runner rather than inferred from a rendered component.
import {
  mergeTranscript,
  TRANSCRIPT_PAGE_SIZE,
  transcriptOlderQuery,
  transcriptReasonRetries,
  transcriptRefreshQuery,
  type SessionTranscript,
} from './transcriptView.ts'

export type TranscriptFetcher = (query: string) => Promise<SessionTranscript>

export type TranscriptTimers = Readonly<{
  set: (callback: () => void, delayMs: number) => unknown
  clear: (handle: unknown) => void
}>

const defaultTimers: TranscriptTimers = {
  set: (callback, delayMs) => setTimeout(callback, delayMs),
  clear: handle => clearTimeout(handle as ReturnType<typeof setTimeout>),
}

/** Waits between retries of a read that came back "indexing", "unreadable", or failed.
 *  Short first, because a first read of a long conversation usually finishes within a
 *  second or two of the five the daemon waits; capped, because a session whose file is
 *  gone should not be asked about forever at a high rate. */
export const TRANSCRIPT_RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 15000, 30000] as const

/** Older pages `loadAll` will walk before giving up on a conversation without end. */
export const TRANSCRIPT_LOAD_ALL_MAX_PAGES = 100

type Job = 'tail' | 'refresh' | 'older'

/** Order a backlog drains in: a fresh tail settles everything, then currency, then history. */
const JOB_ORDER: readonly Job[] = ['tail', 'refresh', 'older']

export class TranscriptLoader {
  private reading: SessionTranscript | null
  private inFlight: Job | null = null
  private readonly pending = new Set<Job>()
  private retryHandle: unknown = null
  private retryAttempt = 0
  private disposed = false
  private olderWaiters: Array<(grew: boolean) => void> = []
  // Plain fields rather than parameter properties: the node test runner only strips
  // types, and a parameter property is code, not a type.
  private readonly fetcher: TranscriptFetcher
  private readonly onChange: (reading: SessionTranscript | null, error: string) => void
  private readonly timers: TranscriptTimers

  constructor(
    fetcher: TranscriptFetcher,
    onChange: (reading: SessionTranscript | null, error: string) => void,
    initial: SessionTranscript | null = null,
    timers: TranscriptTimers = defaultTimers,
  ) {
    this.fetcher = fetcher
    this.onChange = onChange
    this.reading = initial
    this.timers = timers
  }

  get current(): SessionTranscript | null {
    return this.reading
  }

  get busy(): boolean {
    return this.inFlight !== null
  }

  /** Bring the reading up to date. Any number of calls while one is in flight make one. */
  refresh(): void {
    this.request('refresh')
  }

  /** Load the page before the oldest message held. Resolves `true` if messages were added.
   *
   * A call while an older page is already on its way joins that one rather than
   * queueing the page after it: the reader asked for "more above", once. */
  loadOlder(): Promise<boolean> {
    const joining = this.inFlight === 'older' || this.pending.has('older')
    if (!joining && !transcriptOlderQuery(this.reading)) return Promise.resolve(false)
    const done = new Promise<boolean>(resolve => this.olderWaiters.push(resolve))
    if (!joining) this.request('older')
    return done
  }

  /** Walk back to the conversation's first message. Resolves `false` if it could not. */
  async loadAll(): Promise<boolean> {
    for (let page = 0; page < TRANSCRIPT_LOAD_ALL_MAX_PAGES; page++) {
      if (this.disposed) return false
      if (!this.reading?.has_older) return !this.reading?.reason
      if (!(await this.loadOlder())) return !this.reading?.has_older
    }
    return false
  }

  dispose(): void {
    this.disposed = true
    this.clearRetry()
    this.pending.clear()
    this.settleOlder(false)
  }

  private request(job: Job): void {
    if (this.disposed) return
    if (this.inFlight !== null) {
      this.pending.add(job)
      return
    }
    void this.run(job)
  }

  private query(job: Job): string | null {
    if (job === 'older') return transcriptOlderQuery(this.reading)
    if (job === 'tail') return new URLSearchParams({ limit: String(TRANSCRIPT_PAGE_SIZE) }).toString()
    return transcriptRefreshQuery(this.reading)
  }

  private async run(job: Job): Promise<void> {
    this.inFlight = job
    let error = ''
    let grew = false
    const query = this.query(job)
    try {
      if (query !== null) {
        const before = this.reading?.messages.length || 0
        const incoming = await this.fetcher(query)
        if (this.disposed) return
        const merged = mergeTranscript(this.reading, incoming)
        if (merged === null) {
          // The answer cannot be joined to what is held (the daemon rebuilt its
          // reading). Ask for the tail; the column keeps what it has until it lands.
          this.pending.add('tail')
        } else {
          this.reading = merged
          grew = job === 'older' && merged.messages.length > before
        }
        if (incoming.reason && transcriptReasonRetries(incoming.reason)) this.scheduleRetry()
        else this.clearRetry()
      }
    } catch (cause) {
      if (this.disposed) return
      error = cause instanceof Error ? cause.message : String(cause)
      this.scheduleRetry()
    } finally {
      this.inFlight = null
    }
    if (this.disposed) return
    if (job === 'older') this.settleOlder(grew)
    this.onChange(this.reading, error)
    const next = JOB_ORDER.find(candidate => this.pending.has(candidate))
    if (next) {
      this.pending.delete(next)
      void this.run(next)
    }
  }

  private settleOlder(grew: boolean): void {
    const waiters = this.olderWaiters
    this.olderWaiters = []
    for (const resolve of waiters) resolve(grew)
  }

  private clearRetry(): void {
    this.retryAttempt = 0
    if (this.retryHandle !== null) this.timers.clear(this.retryHandle)
    this.retryHandle = null
  }

  private scheduleRetry(): void {
    if (this.retryHandle !== null || this.disposed) return
    const delay = TRANSCRIPT_RETRY_DELAYS_MS[Math.min(this.retryAttempt, TRANSCRIPT_RETRY_DELAYS_MS.length - 1)]
    this.retryAttempt++
    this.retryHandle = this.timers.set(() => {
      this.retryHandle = null
      this.refresh()
    }, delay)
  }
}
