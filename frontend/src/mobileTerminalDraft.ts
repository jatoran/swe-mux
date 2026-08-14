export const MOBILE_TERMINAL_DRAFT_STORAGE_KEY = 'mux.mobileTerminalDrafts.v1'
export const MOBILE_TERMINAL_DRAFT_EVENT = 'mux:mobile-terminal-draft'
export const MOBILE_TERMINAL_DRAFT_MAX_CHARS = 64 * 1024
export const MOBILE_TERMINAL_DRAFT_MAX_ENTRIES = 50
export const MOBILE_TERMINAL_DRAFT_RETENTION_MS = 30 * 24 * 60 * 60 * 1000

export type MobileTerminalDraftEntry = { text: string; updatedAt: number }
export type MobileTerminalDrafts = Record<string, MobileTerminalDraftEntry>
export type MobileTerminalDraftEvent = { sessionId: string; hasDraft: boolean }
export type MobileTerminalInputMode = 'live' | 'read' | 'draft'

export function mobileTerminalInputMode(keyboardOff: boolean, draftOpen: boolean): MobileTerminalInputMode {
  if (draftOpen) return 'draft'
  return keyboardOff ? 'read' : 'live'
}

/** Agent terminals cycle through all three mobile input modes; shells keep the original two. */
export function nextMobileTerminalInputMode(mode: MobileTerminalInputMode, agentSession: boolean): MobileTerminalInputMode {
  if (mode === 'live') return 'read'
  if (mode === 'read') return agentSession ? 'draft' : 'live'
  return 'live'
}

/** Preserve the no-submit Draft contract at the boundary to TerminalPane. */
export async function insertMobileTerminalDraft(
  text: string,
  insert: (value: string, submit: boolean) => Promise<void>,
): Promise<void> {
  if (!text) return
  await insert(text, false)
}

type DraftStorage = Pick<Storage, 'getItem' | 'setItem'>
type DraftBlob = { version: 1; drafts: MobileTerminalDrafts }

const record = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value)

/** Parse, bound, and age a device-local draft registry without consulting live sessions. */
export function parseMobileTerminalDrafts(raw: string | null, now = Date.now()): MobileTerminalDrafts {
  if (!raw) return {}
  let parsed: unknown
  try { parsed = JSON.parse(raw) } catch { return {} }
  if (!record(parsed) || parsed.version !== 1 || !record(parsed.drafts)) return {}
  const cutoff = now - MOBILE_TERMINAL_DRAFT_RETENTION_MS
  const entries: [string, MobileTerminalDraftEntry][] = []
  for (const [sessionId, candidate] of Object.entries(parsed.drafts)) {
    if (!sessionId || !record(candidate) || typeof candidate.text !== 'string' || !candidate.text) continue
    const updatedAt = Number(candidate.updatedAt)
    if (!Number.isFinite(updatedAt) || updatedAt < cutoff) continue
    entries.push([sessionId, {
      text: candidate.text.slice(0, MOBILE_TERMINAL_DRAFT_MAX_CHARS),
      updatedAt: Math.min(updatedAt, now),
    }])
  }
  entries.sort((a, b) => b[1].updatedAt - a[1].updatedAt)
  return Object.fromEntries(entries.slice(0, MOBILE_TERMINAL_DRAFT_MAX_ENTRIES))
}

export function serializeMobileTerminalDrafts(drafts: MobileTerminalDrafts): string {
  const blob: DraftBlob = { version: 1, drafts }
  return JSON.stringify(blob)
}

/**
 * One browser-local registry, keyed by session.
 *
 * Writes are immediate because a mobile browser may suspend without pagehide. The registry
 * is bounded to keep the synchronous localStorage write small. Storage refusal degrades to
 * the in-memory registry, which still survives pane unmounts and workspace tab switches.
 */
export class MobileTerminalDraftStore {
  private drafts: MobileTerminalDrafts | null = null
  private readonly storageProvider: () => DraftStorage | null
  private readonly nowProvider: () => number

  constructor(
    storage: () => DraftStorage | null = () => {
      try { return typeof localStorage === 'undefined' ? null : localStorage } catch { return null }
    },
    now: () => number = Date.now,
  ) {
    this.storageProvider = storage
    this.nowProvider = now
  }

  private load(): MobileTerminalDrafts {
    if (this.drafts) return this.drafts
    let raw: string | null = null
    try { raw = this.storageProvider()?.getItem(MOBILE_TERMINAL_DRAFT_STORAGE_KEY) || null } catch { /* memory fallback */ }
    this.drafts = parseMobileTerminalDrafts(raw, this.nowProvider())
    return this.drafts
  }

  get(sessionId: string): string {
    return this.load()[sessionId]?.text || ''
  }

  has(sessionId: string): boolean {
    return Boolean(this.get(sessionId))
  }

  /**
   * Epoch **seconds** per session currently holding a draft.
   *
   * Seconds rather than the milliseconds stored here, because every other
   * timestamp a session row subtracts from was written by the daemon in
   * seconds; one unit through the whole row engine is what keeps that
   * arithmetic from needing a per-field conversion to be right.
   */
  stamps(): Record<string, number> {
    return Object.fromEntries(
      Object.entries(this.load())
        .filter(([, entry]) => entry.text)
        .map(([sessionId, entry]) => [sessionId, Math.floor(entry.updatedAt / 1000)]),
    )
  }

  set(sessionId: string, text: string): string {
    if (!sessionId) return ''
    const drafts = this.load()
    const hadDraft = Boolean(drafts[sessionId]?.text)
    const kept = text.slice(0, MOBILE_TERMINAL_DRAFT_MAX_CHARS)
    if (kept) drafts[sessionId] = { text: kept, updatedAt: this.nowProvider() }
    else delete drafts[sessionId]
    this.drafts = parseMobileTerminalDrafts(serializeMobileTerminalDrafts(drafts), this.nowProvider())
    try { this.storageProvider()?.setItem(MOBILE_TERMINAL_DRAFT_STORAGE_KEY, serializeMobileTerminalDrafts(this.drafts)) } catch { /* memory fallback */ }
    const storedText = this.drafts[sessionId]?.text || ''
    const hasDraft = Boolean(storedText)
    if (hadDraft !== hasDraft && typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent<MobileTerminalDraftEvent>(MOBILE_TERMINAL_DRAFT_EVENT, {
        detail: { sessionId, hasDraft },
      }))
    }
    return storedText
  }
}

export const mobileTerminalDraftStore = new MobileTerminalDraftStore()
