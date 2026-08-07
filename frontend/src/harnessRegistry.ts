export type HarnessLevel = 'launchable' | 'identified' | 'observed' | 'hooked' | 'managed'

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
   *  stay on the DOM renderer under the `auto` preference — full-screen redraws
   *  can corrupt WebGL scrollback while the viewport is off-tail. Absent from
   *  older daemon payloads, so consumers must default a missing value to the
   *  conservative `true` for agent harnesses. */
  repaints_scrollback?: boolean
}

export interface HarnessDescriptor {
  name: string
  display_name: string
  level: HarnessLevel
  state_sources: string[]
  measurement_source: 'transcript' | 'none'
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

// Keeps the first render coherent while the daemon snapshot is in flight.
// The first application refresh replaces this compatibility seed atomically.
let installed: readonly HarnessDescriptor[] = [
  { name: 'claude', display_name: 'Claude Code', level: 'managed', state_sources: ['transcript', 'hook', 'pty', 'cli_state'], measurement_source: 'transcript', capabilities: { observed: true, transcript: true, measurement: true, lifecycle_hooks: true, pty_delivery: true, external_usage: true, provider_accounts: true, repaints_scrollback: false } },
  { name: 'codex', display_name: 'Codex', level: 'managed', state_sources: ['transcript', 'hook', 'pty'], measurement_source: 'transcript', capabilities: { observed: true, transcript: true, measurement: true, lifecycle_hooks: true, pty_delivery: true, external_usage: true, provider_accounts: true, repaints_scrollback: true } },
  { name: 'omp', display_name: 'oh-my-pi', level: 'managed', state_sources: ['hook', 'transcript', 'pty'], measurement_source: 'transcript', capabilities: { observed: true, transcript: true, measurement: true, lifecycle_hooks: true, pty_delivery: true, external_usage: false, provider_accounts: false, repaints_scrollback: true } },
]

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
export const harnessDisplayName = (name: string): string => harnessDescriptor(name)?.display_name || name
export const harnessNames = (): string[] => installed.map(harness => harness.name)
export const allBackendNames = (): string[] => ['shell', ...harnessNames()]
export const promptDeliveryHarnesses = (): HarnessDescriptor[] => installed.filter(harness => harness.capabilities.pty_delivery)
export const transcriptHarnesses = (): HarnessDescriptor[] => installed.filter(harness => harness.capabilities.transcript)
