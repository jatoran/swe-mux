import { useEffect, useState } from 'preact/hooks'
import { currentProfile, type SettingsProfile } from './deviceSettings.ts'
import { notificationEvents, notificationPreferencesFor, notificationSuppressModes, setNotificationPreferencesFor, type NotificationEvent, type NotificationSuppress } from './notificationPrefs.ts'
import { currentSubscription, disablePush, enablePush, notificationPermission, pushSupported } from './push.ts'

const labels: Record<NotificationEvent, string> = {
  complete: 'Root turn complete',
  waiting: 'Session stopped / waiting for input',
  attention: 'Approval or Q&A',
  failure: 'Failure',
  reset: 'Unexpected quota reset',
}
const profileLabels: Record<SettingsProfile, string> = { desktop: 'Desktop', mobile: 'Mobile' }
const suppressLabels: Record<NotificationSuppress, string> = {
  never: 'never — always notify',
  focused: 'this device has the app open',
  anyDevice: 'any device is in use',
}
const suppressHints: Record<NotificationSuppress, string> = {
  never: 'Every enabled event notifies this device, even while you are looking at it.',
  focused: 'Skipped while this device is on screen and focused; its notification sound covers that.',
  anyDevice: 'Also skipped while you are actively working on another device. Approvals and “waiting for input” are held rather than dropped: if you stop touching the other device, they arrive about 45 seconds later.',
}

export function NotificationPushSettings() {
  const [profile, setProfile] = useState<SettingsProfile>(currentProfile)
  const [prefs, setPrefs] = useState(() => notificationPreferencesFor(profile))
  const [subscribed, setSubscribed] = useState(false)
  const [permission, setPermission] = useState(notificationPermission())
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const sync = () => setPrefs(notificationPreferencesFor(profile))
    sync()
    window.addEventListener('mux:settings-changed', sync)
    return () => window.removeEventListener('mux:settings-changed', sync)
  }, [profile])
  useEffect(() => { void currentSubscription().then(sub => setSubscribed(!!sub)) }, [])

  const change = (next: typeof prefs) => { setPrefs(next); setNotificationPreferencesFor(profile, next) }
  const enable = async () => {
    setBusy(true); setStatus('')
    const result = await enablePush()
    setBusy(false); setPermission(notificationPermission()); setSubscribed(result.ok)
    setStatus(result.ok ? 'Notifications enabled on this device.' : (result.reason || 'Could not enable notifications.'))
  }
  const disable = async () => {
    setBusy(true); await disablePush(); setBusy(false); setSubscribed(false)
    setStatus('Notifications disabled on this device.')
  }

  const supported = pushSupported()
  return <section class="notification-sound-settings"><h3>Push notifications</h3>
    <p>Delivered even when the app is closed or the screen is locked. Which events notify and quiet hours are stored on the server per device class. This device uses the <strong>{profileLabels[currentProfile()]}</strong> profile.</p>
    {!supported && <p class="settings-inline-error">This browser can’t receive push notifications. On iPhone, add the app to the Home Screen first.</p>}
    {supported && <div class="theme-actions">
      {subscribed
        ? <button type="button" disabled={busy} onClick={disable}>Disable on this device</button>
        : <button type="button" disabled={busy} onClick={enable}>Enable on this device</button>}
      {permission === 'denied' && <span class="settings-inline-error">Permission is blocked in the browser’s site settings.</span>}
    </div>}
    {status && <p aria-live="polite">{status}</p>}

    <div class="settings-profile-switch" role="group" aria-label="Editing profile">{(Object.keys(profileLabels) as SettingsProfile[]).map(id => <button type="button" key={id} aria-pressed={profile === id} class={profile === id ? 'is-active' : ''} onClick={() => setProfile(id)}>{profileLabels[id]}{id === currentProfile() ? ' (this device)' : ''}</button>)}</div>
    <label class="check"><span>Enable notifications for {profileLabels[profile]}</span><input type="checkbox" checked={prefs.enabled} onChange={event => change({ ...prefs, enabled: event.currentTarget.checked })} /></label>
    <label class="check"><span>Stay quiet while</span><select aria-label={`When ${profileLabels[profile]} stays quiet`} value={prefs.suppress} onChange={event => change({ ...prefs, suppress: event.currentTarget.value as NotificationSuppress })}>{notificationSuppressModes.map(mode => <option key={mode} value={mode}>{suppressLabels[mode]}</option>)}</select></label>
    <p class="settings-hint">{suppressHints[prefs.suppress]}</p>
    <div class="quiet-hours"><label>Quiet from<input type="time" value={prefs.quietStart} onInput={event => change({ ...prefs, quietStart: event.currentTarget.value })} /></label><label>Until<input type="time" value={prefs.quietEnd} onInput={event => change({ ...prefs, quietEnd: event.currentTarget.value })} /></label></div>
    <div class="notification-event-sounds">
      <strong>Notify on</strong>
      {notificationEvents.map(event => <label class="check" key={event}><input type="checkbox" checked={prefs.events[event]} onChange={changeEvent => change({ ...prefs, events: { ...prefs.events, [event]: changeEvent.currentTarget.checked } })} /><span>{labels[event]}</span></label>)}
    </div>
  </section>
}
