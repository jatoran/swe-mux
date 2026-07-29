// Per-profile push-notification preferences, stored server-side via deviceSettings.
// The shape and defaults mirror settings_store.default_notifications on the daemon,
// which is the side that actually enforces them when deciding whether to push.
import { currentProfile, rawDomain, saveDomain, type SettingsProfile } from './deviceSettings.ts'

export type NotificationEvent = 'complete' | 'waiting' | 'attention' | 'failure' | 'reset'
/** Which presence silences this profile. `anyDevice` also covers the *other* device:
 *  no lock-screen buzz for an approval the user is watching happen at their desk. */
export type NotificationSuppress = 'never' | 'focused' | 'anyDevice'
export interface NotificationPreferences {
  enabled: boolean
  events: Record<NotificationEvent, boolean>
  suppress: NotificationSuppress
  quietStart: string
  quietEnd: string
}

export const notificationEvents: NotificationEvent[] = ['complete', 'waiting', 'attention', 'failure', 'reset']
export const notificationSuppressModes: NotificationSuppress[] = ['never', 'focused', 'anyDevice']

function defaults(profile: SettingsProfile): NotificationPreferences {
  return {
    enabled: true,
    events: { complete: false, waiting: true, attention: true, failure: false, reset: false },
    // The phone is the device that gets notified about work happening elsewhere; the
    // desktop is where that work happens, so "someone is active elsewhere" is not a
    // reason to keep it quiet.
    suppress: profile === 'mobile' ? 'anyDevice' : 'focused',
    quietStart: '',
    quietEnd: '',
  }
}

export function normalizeNotificationPreferences(value: unknown, profile: SettingsProfile = 'mobile'): NotificationPreferences {
  const base = defaults(profile)
  if (!value || typeof value !== 'object') return base
  const raw = value as Record<string, unknown>
  if (typeof raw.enabled === 'boolean') base.enabled = raw.enabled
  if (notificationSuppressModes.includes(raw.suppress as NotificationSuppress)) {
    base.suppress = raw.suppress as NotificationSuppress
  } else if (typeof raw.suppressWhenFocused === 'boolean') {
    // Migration from the boolean this replaced, matching settings_store.py: false was
    // a deliberate "notify me anyway" and is kept; true was the default, so it lands
    // on the profile's new default instead of pinning the old behaviour.
    base.suppress = raw.suppressWhenFocused ? base.suppress : 'never'
  }
  if (typeof raw.quietStart === 'string') base.quietStart = raw.quietStart
  if (typeof raw.quietEnd === 'string') base.quietEnd = raw.quietEnd
  if (raw.events && typeof raw.events === 'object') {
    const events = raw.events as Record<string, unknown>
    for (const event of notificationEvents) if (typeof events[event] === 'boolean') base.events[event] = events[event] as boolean
  }
  return base
}

export function notificationPreferencesFor(profile: SettingsProfile): NotificationPreferences {
  return normalizeNotificationPreferences(rawDomain(profile, 'notifications'), profile)
}

export function notificationPreferences(): NotificationPreferences {
  return notificationPreferencesFor(currentProfile())
}

export function setNotificationPreferencesFor(profile: SettingsProfile, value: NotificationPreferences): void {
  void saveDomain(profile, 'notifications', value as unknown as Record<string, unknown>).catch(() => {/* UI already updated optimistically */})
}
