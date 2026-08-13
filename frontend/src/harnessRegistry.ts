import { HARNESS_REGISTRY_SEED } from './harnessRegistrySeed.ts'

export type HarnessLevel = 'launchable' | 'identified' | 'observed' | 'hooked' | 'managed'

/**
 * Per-harness traits the daemon declares.
 *
 * Every field here is owned by a `HarnessDescriptor` in `src/swe_mux/harness.py`
 * and travels in the registry payload. Nothing in the browser may re-derive one of
 * these from a harness name: that is how the command rail came to type `/name` on
 * oh-my-pi while the daemon knew the CLI wanted `/skill:name`.
 *
 * Optional fields are ones a daemon older than this build may not send. Each
 * consumer defaults a missing value to the conservative answer rather than to
 * `false`.
 */
export interface HarnessCapabilities {
  observed: boolean
  transcript: boolean
  measurement: boolean
  lifecycle_hooks: boolean
  pty_delivery: boolean
  external_usage: boolean
  provider_accounts: boolean
  /** The TUI rewrites content already in xterm scrollback (Codex reflows its
   *  transcript on resize; OMP repaints its tail continuously). Such harnesses
   *  stay on the DOM renderer under the `auto` preference: full-screen redraws
   *  can corrupt WebGL scrollback while the viewport is off-tail. Absent from
   *  older daemon payloads, so consumers must default a missing value to the
   *  conservative `true` for agent harnesses. */
  repaints_scrollback?: boolean
  /** mux dictated the conversation id at spawn, so the session is resumable from
   *  its first moment rather than only after the CLI reports an id. */
  assigns_conversation_id?: boolean
  /** The CLI resolves a conversation's location from its working directory, so a
   *  copied resume command only reopens it when run from the session's cwd. */
  resolves_transcript_by_cwd?: boolean
  /** The daemon implements a fork strategy for this harness. */
  branch?: boolean
  /** WebGL is never safe here, not even under an explicit `webgl` preference.
   *  Distinct from `repaints_scrollback`, which only excludes `auto`. */
  webgl_unsafe?: boolean
  /** The TUI keeps its own scroll viewport, so jumping to the newest output has to
   *  be asked of the application as well as of xterm. */
  owns_scroll_viewport?: boolean
  /** The desktop column envelope applies to this harness. */
  width_envelope?: boolean
  /** Published minimum column count below which desktop panes shrink the font. */
  min_desktop_columns?: number | null
  /** A late OSC 10/11 reply must be dropped: this CLI's startup palette probe
   *  would treat it as composer input. */
  suppresses_late_color_response?: boolean
  /** Finger rows represented by one wheel report forwarded during a touch drag. */
  touch_scroll_rows_per_report?: number
}

export interface HarnessDescriptor {
  name: string
  display_name: string
  level: HarnessLevel
  state_sources: string[]
  /** How measurements are obtained. `database` is a harness that keeps running
   *  totals in its own store rather than in a parseable transcript. Typed as a
   *  string union that must stay in step with `MeasurementSource` in
   *  `harness.py`; a value this union cannot express is what let opencode's
   *  `database` land in a field declared as `'transcript' | 'none'`. */
  measurement_source: 'transcript' | 'database' | 'none'
  /** The plain vendor CLI name, for composing a human-pasteable command. */
  cli_name?: string
  /** Argv tokens preceding a conversation id when resuming. */
  resume_argv?: string[]
  /** What a user types to invoke an authored skill (`/`, `$`, `/skill:`). */
  skill_invocation_prefix?: string
  capabilities: HarnessCapabilities
}

export interface HarnessRegistryPayload {
  version: number
  harnesses: HarnessDescriptor[]
}

const LEVEL: Record<HarnessLevel, number> = {
  launchable: 0,
  identified: 1,
  observed: 2,
  hooked: 3,
  managed: 4,
}

// Keeps the first render coherent while the daemon snapshot is in flight. The
// first application refresh replaces this seed atomically.
//
// Generated from the daemon's own descriptors (`harnessRegistrySeed.ts`), never
// written by hand: the hand-written version of this list had opencode at `hooked`
// with no measurement while its descriptor said `managed` with database
// measurement, and pi missing its `pty` state source, because nothing compared the
// two.
let installed: readonly HarnessDescriptor[] = HARNESS_REGISTRY_SEED.harnesses

export function installHarnessRegistry(payload: HarnessRegistryPayload): void {
  if (payload.version !== 1 || !Array.isArray(payload.harnesses)) throw new Error('unsupported harness registry payload')
  installed = payload.harnesses.map(harness => ({
    ...harness,
    state_sources: [...harness.state_sources],
    capabilities: { ...harness.capabilities },
  }))
}

export const harnesses = (): readonly HarnessDescriptor[] => installed

export const harnessDescriptor = (name: string | undefined): HarnessDescriptor | undefined =>
  installed.find(harness => harness.name === name)

export function isAgentBackend(name: string | undefined): boolean {
  if (!name || name === 'shell') return false
  return installed.length === 0 || harnessDescriptor(name) !== undefined
}

export function harnessAtLeast(name: string | undefined, level: HarnessLevel): boolean {
  const harness = harnessDescriptor(name)
  return harness !== undefined && LEVEL[harness.level] >= LEVEL[level]
}

export const isObservedHarness = (name: string | undefined): boolean => harnessAtLeast(name, 'observed')
export const hasHarnessTranscript = (name: string | undefined): boolean => Boolean(harnessDescriptor(name)?.capabilities.transcript)
export const hasHarnessMeasurement = (name: string | undefined): boolean => Boolean(harnessDescriptor(name)?.capabilities.measurement)
export const deliversHarnessPrompts = (name: string | undefined): boolean => Boolean(harnessDescriptor(name)?.capabilities.pty_delivery)
export const hasExternalUsage = (name: string | undefined): boolean => Boolean(harnessDescriptor(name)?.capabilities.external_usage)
export const hasProviderAccounts = (name: string | undefined): boolean => Boolean(harnessDescriptor(name)?.capabilities.provider_accounts)
/**
 * Whether this backend's TUI rewrites content already in scrollback, which is
 * what makes WebGL risky for it (see `shouldLoadWebgl`). A shell repaints
 * nothing; an unknown agent harness or a payload predating the flag defaults to
 * `true` because the safe renderer is the DOM one.
 */
export function repaintsScrollback(name: string | undefined): boolean {
  if (!isAgentBackend(name)) return false
  const harness = harnessDescriptor(name)
  return harness?.capabilities.repaints_scrollback ?? true
}

/**
 * Whether WebGL must never be used for this pane, including under an explicit
 * `webgl` preference.
 *
 * Separate from `repaintsScrollback`, which only excludes the `auto` preference and
 * stays overridable. A harness here has a live-context failure with no context-loss
 * event and no recovery short of a real resize, so there is nothing an override
 * could usefully mean. An unknown agent harness (or a daemon predating the field)
 * defaults to unsafe, because the DOM renderer is the one with no failure mode.
 */
export function webglUnsafe(name: string | undefined): boolean {
  if (!isAgentBackend(name)) return false
  return harnessDescriptor(name)?.capabilities.webgl_unsafe ?? true
}

/**
 * Whether the application keeps its own scroll viewport, so reaching the newest
 * output has to be asked of it as well as of xterm.
 *
 * A static claim about the TUI. It is not the whole answer: a pane that has
 * negotiated mouse tracking is handed scroll gestures at runtime and must be asked
 * regardless, which callers combine with this.
 */
export const ownsScrollViewport = (name: string | undefined): boolean =>
  Boolean(harnessDescriptor(name)?.capabilities.owns_scroll_viewport)

/** Whether the desktop column envelope applies to this harness. */
export const appliesWidthEnvelope = (name: string | undefined): boolean =>
  Boolean(harnessDescriptor(name)?.capabilities.width_envelope)

/** The vendor-published minimum desktop column count, or 0 when there is none. */
export const minDesktopColumns = (name: string | undefined): number =>
  harnessDescriptor(name)?.capabilities.min_desktop_columns ?? 0

/** Whether a late OSC 10/11 reply must be dropped rather than delivered. */
export const suppressesLateColorResponse = (name: string | undefined): boolean =>
  Boolean(harnessDescriptor(name)?.capabilities.suppresses_late_color_response)

export function applicationTouchScrollProfile(name: string | undefined): {
  rowsPerReport: number
} {
  const capabilities = harnessDescriptor(name)?.capabilities
  return {
    rowsPerReport: Math.max(1, Math.trunc(capabilities?.touch_scroll_rows_per_report ?? 1)),
  }
}

/** Whether the daemon implements a fork strategy for this harness. */
export const supportsBranch = (name: string | undefined): boolean =>
  Boolean(harnessDescriptor(name)?.capabilities.branch)

/** Whether mux dictated the conversation id, making the session resumable at once. */
export const assignsConversationId = (name: string | undefined): boolean =>
  Boolean(harnessDescriptor(name)?.capabilities.assigns_conversation_id)

/** Whether a copied resume command only reopens the conversation from its own cwd. */
export const resolvesTranscriptByCwd = (name: string | undefined): boolean =>
  Boolean(harnessDescriptor(name)?.capabilities.resolves_transcript_by_cwd)

/**
 * What a user types to invoke an authored skill on this harness.
 *
 * Declared by the daemon because it is a per-CLI grammar, not a convention: Codex
 * uses `$`, oh-my-pi namespaces skills under `/skill:`, and the rest use `/`. An
 * unknown harness falls back to `/`, which is the majority spelling and the least
 * surprising thing to type.
 */
export const skillInvocationPrefix = (name: string | undefined): string =>
  harnessDescriptor(name)?.skill_invocation_prefix ?? '/'
export const harnessDisplayName = (name: string): string => harnessDescriptor(name)?.display_name || name
export const harnessNames = (): string[] => installed.map(harness => harness.name)
export const allBackendNames = (): string[] => ['shell', ...harnessNames()]
export const promptDeliveryHarnesses = (): HarnessDescriptor[] => installed.filter(harness => harness.capabilities.pty_delivery)
export const transcriptHarnesses = (): HarnessDescriptor[] => installed.filter(harness => harness.capabilities.transcript)
