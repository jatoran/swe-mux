// Server-persisted UI settings, split into desktop/mobile device-class profiles.
//
// swe-mux is single-user but driven from both a desktop browser and an Android
// phone, which want different sound/notification behaviour. Instead of each
// device keeping its own localStorage (unshareable, and invisible to the
// server-side push sender), settings live on the daemon keyed by device class.
// Every device can read and edit either profile; a device *uses* the profile
// matching its own class at runtime.
//
// The cache is populated once at boot and refreshed when another device edits
// settings (the daemon emits `settings_changed` over the /events socket). Reads
// are synchronous against the cache so hot paths like handleSessionSound stay
// sync; an unloaded cache simply yields defaults.
import { api } from './api.ts'
import { clearProjectRailBlob, railConfigFromBlob, writeRailConfigBlob, type RailBlob, type RailConfig } from './commandRail.ts'
import { resolveRail, type ResolvedRail } from './railScope.ts'

export type SettingsProfile = 'desktop' | 'mobile'
export type SettingsDomain = 'alerts' | 'sounds' | 'notifications' | 'commandRail' | 'fileTree' | 'drawerTabs' | 'sessionRows' | 'sessionTopbar' | 'keyboard'
type ProfileSettings = Partial<Record<SettingsDomain, Record<string, unknown>>>
type AllSettings = Record<SettingsProfile, ProfileSettings>

/** The one device-class breakpoint. Exported so chrome scale (`uiScale.ts`) splits
 *  desktop from mobile on exactly the same line the workspace and these settings do. */
export const MOBILE_QUERY = '(max-width:760px)'
/** Devices whose only keyboard is an on-screen one that covers the layout when it
 *  opens. A separate question from `MOBILE_QUERY`: a narrowed desktop window has a
 *  real keyboard, and a landscape tablet is wider than the breakpoint but not. */
export const SOFT_KEYBOARD_QUERY = '(max-width: 760px), (pointer: coarse)'
const LEGACY_SOUND_KEY = 'swe-mux:session-sounds-v1'

/** True where focusing a field would raise the soft keyboard over the workspace. */
export function hasSoftKeyboard(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.(SOFT_KEYBOARD_QUERY).matches
}

let cache: AllSettings = { desktop: {}, mobile: {} }
let loaded = false
let migrated = false

/** The profile this device uses, from the same breakpoint the workspace uses. */
export function currentProfile(): SettingsProfile {
  return typeof window !== 'undefined' && window.matchMedia?.(MOBILE_QUERY).matches ? 'mobile' : 'desktop'
}

export function settingsLoaded(): boolean { return loaded }

export function rawDomain(profile: SettingsProfile, domain: SettingsDomain): Record<string, unknown> | undefined {
  const value = cache[profile]?.[domain]
  return value && typeof value === 'object' ? value : undefined
}

function emit(): void { window.dispatchEvent(new CustomEvent('mux:settings-changed')) }

export async function loadSettings(): Promise<void> {
  try {
    const data = await api<{ profiles?: Partial<AllSettings> }>('GET', '/api/settings')
    cache = { desktop: data.profiles?.desktop || {}, mobile: data.profiles?.mobile || {} }
    loaded = true
    await migrateLegacySounds()
    emit()
  } catch { /* daemon unreachable: keep whatever is cached, retried on next refresh */ }
}

/** Refetch after a remote edit; fire-and-forget from the events socket. */
export function refreshSettings(): void { void loadSettings() }

export async function saveDomain(
  profile: SettingsProfile,
  domain: SettingsDomain,
  value: Record<string, unknown>,
): Promise<void> {
  cache = { ...cache, [profile]: { ...cache[profile], [domain]: value } }
  emit()
  await api('PUT', `/api/settings/${profile}`, { [domain]: value })
}

// Actions carries its *own* desktop/mobile split inside one blob
// (`RailConfig.layouts`), because the catalog of commands is shared while the
// arrangements are not. Splitting the layouts across this store's two profile
// buckets would make a save two writes, with a window where one device's layout
// references a command the catalog has not got yet — so the rail deliberately
// stays in one canonical bucket and does its own device split.
const RAIL_PROFILE: SettingsProfile = 'desktop'

function railBlob(): RailBlob | undefined {
  const raw = rawDomain(RAIL_PROFILE, 'commandRail')
  return raw && typeof raw === 'object' ? (raw as RailBlob) : undefined
}

/** Effective rail configuration for a project (falls back to the global one). */
export function loadRailConfig(projectId?: string): RailConfig {
  return railConfigFromBlob(railBlob(), projectId)
}

/** Persist an edited configuration to the global scope or a project override. */
export async function saveRailConfig(config: RailConfig, projectId?: string): Promise<void> {
  const next = writeRailConfigBlob(railBlob(), config, projectId)
  await saveDomain(RAIL_PROFILE, 'commandRail', next as unknown as Record<string, unknown>)
}

/** Drop a project's override so it inherits the global rail again. Reverts a
 *  fork to global and removes a delta's additions alike. */
export async function clearProjectRail(projectId: string): Promise<void> {
  const next = clearProjectRailBlob(railBlob(), projectId)
  await saveDomain(RAIL_PROFILE, 'commandRail', next as Record<string, unknown>)
}

/** The raw Actions blob, for the scope-aware editing ops in `railScope.ts`. */
export function currentRailBlob(): RailBlob | undefined {
  return railBlob()
}

/** Effective config plus ownership, for the editors and pin surfaces. */
export function loadResolvedRail(projectId?: string): ResolvedRail {
  return resolveRail(railBlob(), projectId)
}

/** Persist a blob produced by the scope-aware ops (`railScope.ts`). */
export async function saveRailBlob(blob: RailBlob): Promise<void> {
  await saveDomain(RAIL_PROFILE, 'commandRail', blob as unknown as Record<string, unknown>)
}

// The file-tree expand state is deliberately shared across desktop and mobile —
// the same projects are browsed from both, so like Actions it lives in
// one canonical profile bucket rather than duplicated per device class. The blob
// maps a project id to its expanded folder paths: {projectId: string[]}.
const FILE_TREE_PROFILE: SettingsProfile = 'desktop'
type FileTreeBlob = Record<string, string[]>

function fileTreeBlob(): FileTreeBlob {
  const raw = rawDomain(FILE_TREE_PROFILE, 'fileTree')
  return raw && typeof raw === 'object' ? (raw as FileTreeBlob) : {}
}

/** Persisted expanded folder paths for a project (empty until the cache loads). */
export function loadExpandedFolders(projectId: string): string[] {
  const paths = fileTreeBlob()[projectId]
  return Array.isArray(paths) ? paths.filter((path): path is string => typeof path === 'string') : []
}

/** Persist a project's expanded folders, preserving every other project's entry. */
export async function saveExpandedFolders(projectId: string, paths: string[]): Promise<void> {
  const next: FileTreeBlob = { ...fileTreeBlob() }
  if (paths.length) next[projectId] = paths
  else delete next[projectId]
  await saveDomain(FILE_TREE_PROFILE, 'fileTree', next as Record<string, unknown>)
}

// Read-only migration input for the former server-synced flat drawer order. New recursive drawer
// layouts are device-local in `drawerLayout.ts`; this domain remains accepted by the daemon so an
// upgraded browser can seed its first layout without losing the user's former order.
const DRAWER_TAB_PROFILE: SettingsProfile = 'desktop'

/** Persisted tab order, unvalidated. Callers normalize; an unloaded cache yields undefined. */
export function loadDrawerTabOrder(): unknown {
  return rawDomain(DRAWER_TAB_PROFILE, 'drawerTabs')?.order
}

// One-time import of the pre-server sound blob into this device's profile, so an
// upgrade keeps the sounds the user already configured instead of resetting.
async function migrateLegacySounds(): Promise<void> {
  if (migrated) return
  migrated = true
  const profile = currentProfile()
  if (rawDomain(profile, 'sounds')) return
  let legacy: string | null = null
  try { legacy = localStorage.getItem(LEGACY_SOUND_KEY) } catch { return }
  if (!legacy) return
  try {
    const parsed = JSON.parse(legacy)
    if (parsed && typeof parsed === 'object') {
      await saveDomain(profile, 'sounds', parsed as Record<string, unknown>)
      try { localStorage.removeItem(LEGACY_SOUND_KEY) } catch { /* private mode */ }
    }
  } catch { /* corrupt legacy blob: drop it */ }
}
