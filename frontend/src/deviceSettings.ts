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
import { clearProjectRailBlob, railHasProjectOverride, railItemsFromBlob, writeRailBlob, type RailBlob, type RailItem } from './commandRail.ts'

export type SettingsProfile = 'desktop' | 'mobile'
export type SettingsDomain = 'sounds' | 'notifications' | 'commandRail' | 'fileTree' | 'drawerTabs'
type ProfileSettings = Partial<Record<SettingsDomain, Record<string, unknown>>>
type AllSettings = Record<SettingsProfile, ProfileSettings>

const MOBILE_QUERY = '(max-width:760px)'
const LEGACY_SOUND_KEY = 'swe-mux:session-sounds-v1'

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

// The command rail is a single shared configuration (per-item platform/backend
// tags handle device differences, so adding an item lands on both desktop and
// mobile by default). It is stored under one canonical profile bucket rather
// than duplicated per device class.
const RAIL_PROFILE: SettingsProfile = 'desktop'

function railBlob(): RailBlob | undefined {
  const raw = rawDomain(RAIL_PROFILE, 'commandRail')
  return raw && typeof raw === 'object' ? (raw as RailBlob) : undefined
}

/** Effective rail items for a project (falls back to the global list). */
export function loadRailItems(projectId?: string): RailItem[] {
  return railItemsFromBlob(railBlob(), projectId)
}

/** Persist an edited item list to the global list or a project override. */
export async function saveRailItems(items: RailItem[], projectId?: string): Promise<void> {
  const next = writeRailBlob(railBlob(), items, projectId)
  await saveDomain(RAIL_PROFILE, 'commandRail', next as Record<string, unknown>)
}

export function projectRailIsCustom(projectId: string): boolean {
  return railHasProjectOverride(railBlob(), projectId)
}

/** Drop a project's override so it inherits the global rail again. */
export async function clearProjectRail(projectId: string): Promise<void> {
  const next = clearProjectRailBlob(railBlob(), projectId)
  await saveDomain(RAIL_PROFILE, 'commandRail', next as Record<string, unknown>)
}

// The file-tree expand state is deliberately shared across desktop and mobile —
// the same projects are browsed from both — so like the command rail it lives in
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

// The drawer's tab order is one arrangement shared by the strip and the desktop rail, and —
// like the command rail and the file tree — it says which surfaces the user reaches for rather
// than anything about screen size, so it lives in one canonical bucket instead of per device
// class. The blob is `{order: DrawerTabId[]}`; validation is the browser's (`drawerTabOrder.ts`),
// which is why the daemon stores it opaquely.
const DRAWER_TAB_PROFILE: SettingsProfile = 'desktop'

/** Persisted tab order, unvalidated. Callers normalize; an unloaded cache yields undefined. */
export function loadDrawerTabOrder(): unknown {
  return rawDomain(DRAWER_TAB_PROFILE, 'drawerTabs')?.order
}

export async function saveDrawerTabOrder(order: string[]): Promise<void> {
  await saveDomain(DRAWER_TAB_PROFILE, 'drawerTabs', { order })
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
