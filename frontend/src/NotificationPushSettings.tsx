import { useEffect, useState } from 'preact/hooks'
import { alertPreferencesFor, setAlertPreferencesFor } from './alertPrefs.ts'
import { currentProfile, type SettingsProfile } from './deviceSettings.ts'
import {
  notificationEvents,
  notificationPreferencesFor,
  notificationSuppressModes,
  setNotificationPreferencesFor,
  type NotificationEvent,
  type NotificationSuppress,
} from './notificationPrefs.ts'
import { currentSubscription, disablePush, enablePush, notificationPermission, pushSupported } from './push.ts'
import {
  lastSoundReason,
  safeSoundFile,
  satisfyingSounds,
  setSoundPreferencesFor,
  soundPreferencesFor,
  testSessionSound,
  type SoundEvent,
  type SoundId,
} from './sessionSounds.ts'

const eventLabels: Record<NotificationEvent, string> = {
  complete: 'Root turn complete',
  waiting: 'Waiting for input',
  attention: 'Approval or question',
  failure: 'Failure',
  reset: 'Unexpected quota reset',
}
const profileLabels: Record<SettingsProfile, string> = { desktop: 'Desktop', mobile: 'Mobile' }
const suppressLabels: Record<NotificationSuppress, string> = {
  never: 'Never - always push',
  focused: 'This device has the app open',
  anyDevice: 'Any device is in use',
}
const suppressHints: Record<NotificationSuppress, string> = {
  never: 'Every enabled push event is delivered, even while you are looking at the app.',
  focused: 'Push is skipped while this device is focused. An enabled foreground sound can cover that moment.',
  anyDevice: 'Push is also suppressed while you are active elsewhere. Attention and waiting alerts are held briefly in case you step away.',
}
const customSoundOption = { id: 'custom' as const, label: 'Custom', description: 'your uploaded sound', glyph: '♪' }

export function NotificationAlertSettings() {
  const [profile, setProfile] = useState<SettingsProfile>(currentProfile)
  const [alerts, setAlerts] = useState(() => alertPreferencesFor(profile))
  const [push, setPush] = useState(() => notificationPreferencesFor(profile))
  const [sounds, setSounds] = useState(() => soundPreferencesFor(profile))
  const [subscribed, setSubscribed] = useState(false)
  const [permission, setPermission] = useState(notificationPermission())
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [lastReason, setLastReason] = useState(lastSoundReason)
  const [soundError, setSoundError] = useState('')

  useEffect(() => {
    const sync = () => {
      setAlerts(alertPreferencesFor(profile))
      setPush(notificationPreferencesFor(profile))
      setSounds(soundPreferencesFor(profile))
    }
    sync()
    window.addEventListener('mux:settings-changed', sync)
    return () => window.removeEventListener('mux:settings-changed', sync)
  }, [profile])
  useEffect(() => { void currentSubscription().then(subscription => setSubscribed(Boolean(subscription))) }, [])
  useEffect(() => {
    const listener = (event: Event) => setLastReason((event as CustomEvent).detail.reason)
    window.addEventListener('mux:sound-fired', listener)
    return () => window.removeEventListener('mux:sound-fired', listener)
  }, [])

  const changeAlerts = (next: typeof alerts) => { setAlerts(next); setAlertPreferencesFor(profile, next) }
  const changePush = (next: typeof push) => { setPush(next); setNotificationPreferencesFor(profile, next) }
  const changeSounds = (next: typeof sounds) => { setSounds(next); setSoundPreferencesFor(profile, next) }
  const preview = (next: typeof sounds, soundId: SoundId) => {
    void testSessionSound(next, soundId)
      .then(() => setSoundError(''))
      .catch(() => setSoundError('Could not play this sound. Check this site’s audio permission and try again.'))
  }
  const soundOptions = sounds.customSound ? [...satisfyingSounds, customSoundOption] : satisfyingSounds
  const chooseSound = (event: SoundEvent, value: string) => {
    if (value === 'off') {
      changeSounds({ ...sounds, events: { ...sounds.events, [event]: false } })
      return
    }
    const soundId = value as SoundId
    const next = {
      ...sounds,
      events: { ...sounds.events, [event]: true },
      eventSounds: { ...sounds.eventSounds, [event]: soundId },
    }
    changeSounds(next)
    preview(next, soundId)
  }
  const removeCustom = () => {
    const eventSounds = Object.fromEntries(
      Object.entries(sounds.eventSounds).map(([event, soundId]) => [event, soundId === 'custom' ? 'two-tone' : soundId]),
    ) as Record<SoundEvent, SoundId>
    changeSounds({ ...sounds, customSound: undefined, eventSounds })
  }
  const subscribe = async () => {
    setBusy(true); setStatus('')
    const result = await enablePush()
    setBusy(false); setPermission(notificationPermission()); setSubscribed(result.ok)
    setStatus(result.ok ? 'This browser is subscribed.' : (result.reason || 'Could not subscribe this browser.'))
  }
  const unsubscribe = async () => {
    setBusy(true); await disablePush(); setBusy(false); setSubscribed(false)
    setStatus('This browser is unsubscribed. Your profile choices are unchanged.')
  }

  const supported = pushSupported()
  const alertsMuted = !alerts.enabled
  const soundMuted = alertsMuted || !sounds.enabled
  const pushMuted = alertsMuted || !push.enabled

  return <section class="alert-settings">
    <h3>Alerts</h3>
    <p>Sounds and push are delivery channels for the same agent events. The alert history remains available in the Notifications panel even while delivery is muted.</p>

    <div class="settings-profile-switch" role="group" aria-label="Editing alert profile">
      {(Object.keys(profileLabels) as SettingsProfile[]).map(id => <button type="button" key={id} aria-pressed={profile === id} class={profile === id ? 'is-active' : ''} onClick={() => setProfile(id)}>{profileLabels[id]}{id === currentProfile() ? ' (this device)' : ''}</button>)}
    </div>

    <div class="alert-master">
      <div><strong>Enable alerts for {profileLabels[profile]}</strong><p>One mute switch for every interruptive alert on this device class. Channel and event choices are preserved while muted.</p></div>
      <input aria-label={`Enable alerts for ${profileLabels[profile]}`} type="checkbox" checked={alerts.enabled} onChange={event => changeAlerts({ ...alerts, enabled: event.currentTarget.checked })} />
    </div>

    <div class={`alert-controls${alertsMuted ? ' is-muted' : ''}`}>
      <div class="alert-channel-grid">
        <article>
          <label class="check"><span><strong>Sound in the open app</strong><small>Short foreground chimes from this browser.</small></span><input type="checkbox" disabled={alertsMuted} checked={sounds.enabled} onChange={event => changeSounds({ ...sounds, enabled: event.currentTarget.checked })} /></label>
          <label>Volume<input aria-label="Alert sound volume" type="range" min="0" max="1" step=".05" disabled={soundMuted} value={sounds.volume} onInput={event => changeSounds({ ...sounds, volume: Number(event.currentTarget.value) })} /></label>
        </article>
        <article>
          <label class="check"><span><strong>Push in the background</strong><small>System notifications when the app is closed, hidden, or locked.</small></span><input type="checkbox" disabled={alertsMuted} checked={push.enabled} onChange={event => changePush({ ...push, enabled: event.currentTarget.checked })} /></label>
          <label>Stay quiet while<select aria-label={`When ${profileLabels[profile]} push stays quiet`} disabled={pushMuted} value={push.suppress} onChange={event => changePush({ ...push, suppress: event.currentTarget.value as NotificationSuppress })}>{notificationSuppressModes.map(mode => <option key={mode} value={mode}>{suppressLabels[mode]}</option>)}</select></label>
          <p class="settings-hint">{suppressHints[push.suppress]}</p>
        </article>
      </div>

      <div class="alert-subsection">
        <strong>Quiet hours</strong>
        <p>One schedule applies to both delivery channels. Leave both times empty for no quiet period.</p>
        <div class="quiet-hours"><label>Quiet from<input type="time" disabled={alertsMuted} value={alerts.quietStart} onInput={event => changeAlerts({ ...alerts, quietStart: event.currentTarget.value })} /></label><label>Until<input type="time" disabled={alertsMuted} value={alerts.quietEnd} onInput={event => changeAlerts({ ...alerts, quietEnd: event.currentTarget.value })} /></label></div>
      </div>

      <div class="alert-subsection">
        <strong>Events</strong>
        <p>Choose a foreground sound or Off for each event, and independently choose whether it can produce push.</p>
        <div class="alert-event-table" role="group" aria-label="Alert delivery by event">
          <div class="alert-event-header" aria-hidden="true"><span>Event</span><span>Sound</span><span>Push</span><span></span></div>
          {notificationEvents.map(event => {
            const soundEvent = event as SoundEvent
            return <div class="alert-event-row" key={event}>
              <strong>{eventLabels[event]}</strong>
              <select aria-label={`Sound for ${eventLabels[event]}`} disabled={soundMuted} value={sounds.events[soundEvent] ? sounds.eventSounds[soundEvent] : 'off'} onChange={changeEvent => chooseSound(soundEvent, changeEvent.currentTarget.value)}>
                <option value="off">Off</option>
                {soundOptions.map(sound => <option key={sound.id} value={sound.id}>{sound.label}</option>)}
              </select>
              <input aria-label={`Push for ${eventLabels[event]}`} type="checkbox" disabled={pushMuted} checked={push.events[event]} onChange={changeEvent => changePush({ ...push, events: { ...push.events, [event]: changeEvent.currentTarget.checked } })} />
              <button type="button" disabled={soundMuted || !sounds.events[soundEvent]} aria-label={`Preview sound for ${eventLabels[event]}`} onClick={() => preview(sounds, sounds.eventSounds[soundEvent])}>Preview</button>
            </div>
          })}
        </div>
      </div>
    </div>

    <div class="alert-subsection">
      <strong>Sound library</strong>
      <p>Previewing a sound never changes the alert master or channel switches.</p>
      <div class="sound-preset-grid" role="group" aria-label="Available alert sounds">
        {soundOptions.map(sound => <button type="button" key={sound.id} aria-label={`Preview ${sound.label}`} onClick={() => preview(sounds, sound.id)}><span aria-hidden="true">{sound.glyph}</span><strong>{sound.label}</strong><small>{sound.description}</small></button>)}
      </div>
      <label>Custom sound<input type="file" accept="audio/*" onChange={event => { const file = event.currentTarget.files?.[0]; if (file) void safeSoundFile(file).then(customSound => { const next = { ...sounds, customSound }; changeSounds(next); preview(next, 'custom') }).catch(cause => setSoundError(cause.message)) }} /></label>
      {sounds.customSound && <div class="theme-actions"><button type="button" onClick={removeCustom}>Remove custom sound</button></div>}
      <p aria-live="polite">Last sound: {lastReason}</p>
      {soundError && <p class="settings-inline-error">{soundError}</p>}
    </div>

    <div class="alert-subsection alert-browser-status">
      <div><strong>This browser’s push capability</strong><p>{supported ? (subscribed ? 'Subscribed. Push still follows the selected profile, master, event, quiet-hour, and presence settings.' : 'Not subscribed. Profile choices are preserved and can be configured before subscribing.') : 'Push is unavailable in this browser. On iPhone, add the app to the Home Screen first.'}</p></div>
      {supported && (subscribed
        ? <button type="button" disabled={busy} onClick={unsubscribe}>Unsubscribe browser</button>
        : <button type="button" disabled={busy} onClick={subscribe}>Subscribe browser</button>)}
      {permission === 'denied' && <p class="settings-inline-error">Permission is blocked in the browser’s site settings.</p>}
      {status && <p aria-live="polite">{status}</p>}
    </div>
  </section>
}

// Compatibility for code importing the former channel-specific component.
export const NotificationPushSettings = NotificationAlertSettings
