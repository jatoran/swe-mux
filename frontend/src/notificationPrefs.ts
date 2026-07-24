// Per-profile push-notification preferences, stored server-side via deviceSettings.
// The shape and defaults mirror settings_store.default_notifications on the daemon,
// which is the side that actually enforces them when deciding whether to push.
import { currentProfile, rawDomain, saveDomain, type SettingsProfile } from './deviceSettings.ts'

export type NotificationEvent = 'complete' | 'waiting' | 'attention' | 'failure' | 'reset'
export interface NotificationPreferences {
  enabled: boolean
  events: Record<NotificationEvent, boolean>
  suppressWhenFocused: boolean
  quietStart: string
  quietEnd: string
}

export const notificationEvents: NotificationEvent[] = ['complete', 'waiting', 'attention', 'failure', 'reset']

function defaults(): NotificationPreferences {
  return {
    enabled: true,
    events: { complete: false, waiting: true, attention: true, failure: false, reset: false },
    suppressWhenFocused: true,
    quietStart: '',
    quietEnd: '',
  }
}

export function normalizeNotificationPreferences(value: unknown): NotificationPreferences {
  const base = defaults()
  if (!value || typeof value !== 'object') return base
  const raw = value as Record<string, unknown>
  if (typeof raw.enabled === 'boolean') base.enabled = raw.enabled
  if (typeof raw.suppressWhenFocused === 'boolean') base.suppressWhenFocused = raw.suppressWhenFocused
  if (typeof raw.quietStart === 'string') base.quietStart = raw.quietStart
  if (typeof raw.quietEnd === 'string') base.quietEnd = raw.quietEnd
  if (raw.events && typeof raw.events === 'object') {
    const events = raw.events as Record<string, unknown>
    for (const event of notificationEvents) if (typeof events[event] === 'boolean') base.events[event] = events[event] as boolean
  }
  return base
}

export function notificationPreferencesFor(profile: SettingsProfile): NotificationPreferences {
  return normalizeNotificationPreferences(rawDomain(profile, 'notifications'))
}

export function notificationPreferences(): NotificationPreferences {
  return notificationPreferencesFor(currentProfile())
}

export function setNotificationPreferencesFor(profile: SettingsProfile, value: NotificationPreferences): void {
  void saveDomain(profile, 'notifications', value as unknown as Record<string, unknown>).catch(() => {/* UI already updated optimistically */})
}
