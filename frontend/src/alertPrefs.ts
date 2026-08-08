import { currentProfile, rawDomain, saveDomain, type SettingsProfile } from './deviceSettings.ts'

export interface AlertPreferences {
  enabled: boolean
  quietStart: string
  quietEnd: string
}

type LegacyPreferences = Record<string, unknown> | undefined

function quietHours(value: LegacyPreferences): [string, string] {
  return [
    typeof value?.quietStart === 'string' ? value.quietStart : '',
    typeof value?.quietEnd === 'string' ? value.quietEnd : '',
  ]
}

/**
 * Build the shared policy without rewriting old profiles on load. The first edit
 * persists an explicit `alerts` domain; until then, either legacy channel being on
 * keeps alerts on and the active channel's former quiet hours are preserved.
 */
export function normalizeAlertPreferences(
  value: unknown,
  notifications?: LegacyPreferences,
  sounds?: LegacyPreferences,
): AlertPreferences {
  const pushEnabled = typeof notifications?.enabled === 'boolean' ? notifications.enabled : true
  const soundEnabled = sounds?.enabled === true
  const pushQuiet = quietHours(notifications)
  const soundQuiet = quietHours(sounds)
  const inheritedQuiet = pushEnabled && pushQuiet.some(Boolean)
    ? pushQuiet
    : soundEnabled && soundQuiet.some(Boolean)
      ? soundQuiet
      : pushQuiet.some(Boolean) ? pushQuiet : soundQuiet
  const result: AlertPreferences = {
    enabled: pushEnabled || soundEnabled,
    quietStart: inheritedQuiet[0],
    quietEnd: inheritedQuiet[1],
  }
  if (!value || typeof value !== 'object') return result
  const raw = value as Record<string, unknown>
  if (typeof raw.enabled === 'boolean') result.enabled = raw.enabled
  if (typeof raw.quietStart === 'string') result.quietStart = raw.quietStart
  if (typeof raw.quietEnd === 'string') result.quietEnd = raw.quietEnd
  return result
}

export function alertPreferencesFor(profile: SettingsProfile): AlertPreferences {
  return normalizeAlertPreferences(
    rawDomain(profile, 'alerts'),
    rawDomain(profile, 'notifications'),
    rawDomain(profile, 'sounds'),
  )
}

export function alertPreferences(): AlertPreferences {
  return alertPreferencesFor(currentProfile())
}

export function setAlertPreferencesFor(profile: SettingsProfile, value: AlertPreferences): void {
  void saveDomain(profile, 'alerts', value as unknown as Record<string, unknown>).catch(() => {
    /* UI already updated optimistically; a later edit retries persistence. */
  })
}

function minutes(value: string): number | null {
  const match = /^(\d{2}):(\d{2})$/.exec(value)
  return match ? Number(match[1]) * 60 + Number(match[2]) : null
}

export function isAlertQuietTime(preferences: AlertPreferences, date = new Date()): boolean {
  const start = minutes(preferences.quietStart)
  const end = minutes(preferences.quietEnd)
  if (start === null || end === null || start === end) return false
  const now = date.getHours() * 60 + date.getMinutes()
  return start < end ? now >= start && now < end : now >= start || now < end
}
