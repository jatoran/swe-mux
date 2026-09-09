/** Browser-local interaction policy. Viewport size never identifies a device. */
export type DeviceProfile = 'mobile' | 'desktop'
export type DevicePreference = 'auto' | DeviceProfile
export const DEVICE_PREFERENCE_KEY = 'mux.device-mode.v1'
export const DEVICE_DIAGNOSTICS_KEY = 'mux.device-mode-diagnostics.v1'
export const DEVICE_MODE_EVENT = 'mux:device-mode-changed'
export const NARROW_QUERY = '(max-width:760px)'
export const COARSE_QUERY = '(pointer:coarse)'
export const HOVER_QUERY = '(hover: hover)'

export type DeviceSignals = { narrow: boolean; coarse: boolean; hover: boolean }
export type DeviceMode = DeviceSignals & {
  preference: DevicePreference
  profile: DeviceProfile
  layout: DeviceProfile
  reason: 'override' | 'touch-primary' | 'desktop-primary'
}

export function parseDevicePreference(value: unknown): DevicePreference {
  return value === 'mobile' || value === 'desktop' ? value : 'auto'
}

export function resolveDeviceMode(signals: DeviceSignals, preference: DevicePreference = 'auto'): DeviceMode {
  const touchPrimary = signals.coarse && !signals.hover
  const profile = preference === 'auto' ? (touchPrimary ? 'mobile' : 'desktop') : preference
  return {
    ...signals, preference, profile,
    // Narrow desktop windows still need compact navigation, but keep their own settings.
    layout: preference === 'desktop' ? 'desktop' : profile === 'mobile' || signals.narrow ? 'mobile' : 'desktop',
    reason: preference !== 'auto' ? 'override' : touchPrimary ? 'touch-primary' : 'desktop-primary',
  }
}

let owner: Window | undefined
let preference: DevicePreference = 'auto'
let previous: DeviceMode | undefined
let stop: (() => void) | undefined
let sequence = 0
const pageId = Date.now().toString(36) + Math.random().toString(36).slice(2, 8)

function storedPreference(): DevicePreference {
  try { return parseDevicePreference(window.localStorage.getItem(DEVICE_PREFERENCE_KEY)) }
  catch { return 'auto' }
}

function matches(query: string): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.(query).matches
}

export function deviceMode(): DeviceMode {
  return resolveDeviceMode({ narrow: matches(NARROW_QUERY), coarse: matches(COARSE_QUERY), hover: matches(HOVER_QUERY) },
    typeof window !== 'undefined' && owner === window ? preference : storedPreferenceSafe())
}

function storedPreferenceSafe(): DevicePreference {
  return typeof window === 'undefined' ? 'auto' : storedPreference()
}

export function mobileLayout(): boolean { return deviceMode().layout === 'mobile' }

/** A layout override cannot remove a touch device's keyboard/clipboard affordances. */
export function touchInput(): boolean { return matches(COARSE_QUERY) }

function record(operation: string, mode: DeviceMode, failed = false): void {
  const entry = {
    at: new Date().toISOString(), severity: failed ? 'warning' : 'info', component: 'device-mode',
    operation, pageId, sequence: ++sequence, ...mode,
    width: window.innerWidth, height: window.innerHeight,
  }
  try {
    const stored = window.localStorage.getItem(DEVICE_DIAGNOSTICS_KEY)
    let raw: unknown
    try { raw = JSON.parse(stored || '[]') } catch { raw = [] }
    // Keep at most 64 small records; discard malformed and oversized entries.
    const rows = Array.isArray(raw) ? raw.filter(row => row && typeof row === 'object'
      && row.component === 'device-mode' && JSON.stringify(row).length < 1024).slice(-63) : []
    window.localStorage.setItem(DEVICE_DIAGNOSTICS_KEY, JSON.stringify([...rows, entry]))
  } catch { console.warn('[device-mode] Diagnostic storage unavailable', entry) }
  if (failed) console.warn('[device-mode] Preference applies until this page closes', entry)
}

function update(operation: string): void {
  const next = deviceMode()
  document.documentElement.dataset.workspaceLayout = next.layout
  document.documentElement.dataset.deviceProfile = next.profile
  document.documentElement.dataset.touchInput = String(next.coarse)
  if (JSON.stringify(next) === JSON.stringify(previous)) return
  previous = next
  record(operation, next)
  window.dispatchEvent(new CustomEvent(DEVICE_MODE_EVENT, { detail: next }))
  // Existing settings consumers already use this event for profile-dependent state.
  window.dispatchEvent(new CustomEvent('mux:settings-changed'))
}

/** Install once before rendering, also used by isolated component harnesses. */
export function initializeDeviceMode(): void {
  if (typeof window === 'undefined' || typeof document === 'undefined' || !window.matchMedia || owner === window) return
  stop?.()
  owner = window
  preference = storedPreference()
  previous = undefined
  const queries = [NARROW_QUERY, COARSE_QUERY, HOVER_QUERY].map(query => window.matchMedia(query))
  const changed = () => update('capabilities-changed')
  const storage = (event: StorageEvent) => {
    if (event.key !== null && event.key !== DEVICE_PREFERENCE_KEY) return
    preference = storedPreference()
    update('preference-synced')
  }
  for (const query of queries) query.addEventListener('change', changed)
  window.addEventListener('storage', storage)
  window.addEventListener('pageshow', changed)
  stop = () => {
    for (const query of queries) query.removeEventListener('change', changed)
    owner?.removeEventListener('storage', storage)
    owner?.removeEventListener('pageshow', changed)
  }
  update('initialized')
}

/** Returns false only when persistence failed; the current page still applies the choice. */
export function setDevicePreference(value: DevicePreference): boolean {
  initializeDeviceMode()
  preference = parseDevicePreference(value)
  let persisted = true
  try { window.localStorage.setItem(DEVICE_PREFERENCE_KEY, preference) }
  catch { persisted = false }
  update('preference-changed')
  if (!persisted) record('preference-storage-failed', deviceMode(), true)
  return persisted
}

export function watchDeviceMode(callback: (mode: DeviceMode) => void): () => void {
  initializeDeviceMode()
  const changed = () => callback(deviceMode())
  window.addEventListener(DEVICE_MODE_EVENT, changed)
  changed()
  return () => window.removeEventListener(DEVICE_MODE_EVENT, changed)
}

initializeDeviceMode()
