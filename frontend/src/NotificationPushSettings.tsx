import { rich, t } from './localization/index.ts'
import { useEffect, useState } from 'preact/hooks'
import { alertPreferencesFor, setAlertPreferencesFor } from './alertPrefs.ts'
import { Dropdown } from './Dropdown'
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
  get complete() { return t("NotificationPushSettings.14710cfb88") },
  get waiting() { return t("NotificationPushSettings.9259faf308") },
  get attention() { return t("NotificationPushSettings.65508938a3") },
  failure: 'Failure',
  get reset() { return t("NotificationPushSettings.673bfd80f4") },
}
const profileLabels: Record<SettingsProfile, string> = { desktop: 'Desktop', mobile: 'Mobile' }
const suppressLabels: Record<NotificationSuppress, string> = {
  get never() { return t("NotificationPushSettings.401cfdba25") },
  get focused() { return t("NotificationPushSettings.81e80560b2") },
  get anyDevice() { return t("NotificationPushSettings.ebc1a9be5a") },
}
const suppressHints: Record<NotificationSuppress, string> = {
  get never() { return t("NotificationPushSettings.66b4d57d0d") },
  get focused() { return t("NotificationPushSettings.8a42bcf8d5") },
  get anyDevice() { return t("NotificationPushSettings.575c8da61d") },
}
const customSoundOption = { id: 'custom' as const, get label() { return t("NotificationPushSettings.494ca78f73") }, get description() { return t("NotificationPushSettings.914dffe72b") }, glyph: '♪' }

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
      .catch(() => setSoundError(t("NotificationPushSettings.6ff39fbeba")))
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
    setStatus(result.ok ? t("NotificationPushSettings.43f87ef075") : (result.reason || t("NotificationPushSettings.128c587efe")))
  }
  const unsubscribe = async () => {
    setBusy(true); await disablePush(); setBusy(false); setSubscribed(false)
    setStatus(t("NotificationPushSettings.b5ee5adcaf"))
  }

  const supported = pushSupported()
  const alertsMuted = !alerts.enabled
  const soundMuted = alertsMuted || !sounds.enabled
  const pushMuted = alertsMuted || !push.enabled

  return <section class="alert-settings">
    <h3 data-heading-message="NotificationPushSettings.f62e9267d1">{t("NotificationPushSettings.f62e9267d1")}</h3>
    <p>{t("NotificationPushSettings.7811c94990")}</p>

    <div class="settings-profile-switch" role="group" aria-label={t("NotificationPushSettings.4b3f1a4ab1")}>
      {(Object.keys(profileLabels) as SettingsProfile[]).map(id => <button type="button" key={id} aria-pressed={profile === id} class={profile === id ? 'is-active' : ''} onClick={() => setProfile(id)}>{profileLabels[id]}{id === currentProfile() ? t("NotificationPushSettings.41de3fbdea") : ''}</button>)}
    </div>

    <div class="alert-master" data-setting="alerts_enabled">
      <div><strong>{rich("NotificationPushSettings.60849e674a", { value1: profileLabels[profile] })}</strong><p>{t("NotificationPushSettings.c560392fb6")}</p></div>
      <input aria-label={t("NotificationPushSettings.60849e674a", { value1: profileLabels[profile] })} type="checkbox" checked={alerts.enabled} onChange={event => changeAlerts({ ...alerts, enabled: event.currentTarget.checked })} />
    </div>

    <div class={`alert-controls${alertsMuted ? ' is-muted' : ''}`}>
      <div class="alert-channel-grid">
        <article>
          <label class="check"><span><strong>{t("NotificationPushSettings.b4c963cccb")}</strong><small>{t("NotificationPushSettings.5c8143b493")}</small></span><input type="checkbox" disabled={alertsMuted} checked={sounds.enabled} onChange={event => changeSounds({ ...sounds, enabled: event.currentTarget.checked })} /></label>
          <label>{rich("NotificationPushSettings.6ac676b4d1", { value1: <input aria-label={t("NotificationPushSettings.ad7c20a1af")} type="range" min="0" max="1" step=".05" disabled={soundMuted} value={sounds.volume} onInput={event => changeSounds({ ...sounds, volume: Number(event.currentTarget.value) })} /> })}</label>
        </article>
        <article>
          <label class="check"><span><strong>{t("NotificationPushSettings.ff2e147b76")}</strong><small>{t("NotificationPushSettings.c9bbb72c72")}</small></span><input type="checkbox" disabled={alertsMuted} checked={push.enabled} onChange={event => changePush({ ...push, enabled: event.currentTarget.checked })} /></label>
          <label>{rich("NotificationPushSettings.689af4484d", { value1: <Dropdown ariaLabel={t("NotificationPushSettings.bc7f29ea46", { value1: profileLabels[profile] })} disabled={pushMuted} value={push.suppress} onChange={value => changePush({ ...push, suppress: value as NotificationSuppress })} options={notificationSuppressModes.map(mode => ({ value: mode, label: suppressLabels[mode] }))} /> })}</label>
          <p class="settings-hint">{suppressHints[push.suppress]}</p>
        </article>
      </div>

      <div class="alert-subsection">
        <strong>{t("NotificationPushSettings.bf0671dd69")}</strong>
        <p>{t("NotificationPushSettings.e8b7b57ef8")}</p>
        <div class="quiet-hours"><label>{rich("NotificationPushSettings.32be2f94a7", { value1: <input type="time" disabled={alertsMuted} value={alerts.quietStart} onInput={event => changeAlerts({ ...alerts, quietStart: event.currentTarget.value })} /> })}</label><label>{rich("NotificationPushSettings.6c5bf405fb", { value1: <input type="time" disabled={alertsMuted} value={alerts.quietEnd} onInput={event => changeAlerts({ ...alerts, quietEnd: event.currentTarget.value })} /> })}</label></div>
      </div>

      <div class="alert-subsection">
        <strong>{t("NotificationPushSettings.8d14f6e72d")}</strong>
        <p>{t("NotificationPushSettings.96fe6fac66")}</p>
        <div class="alert-event-table" role="group" aria-label={t("NotificationPushSettings.edd289d3bc")}>
          <div class="alert-event-header" aria-hidden="true"><span>{t("NotificationPushSettings.4e1f49a9c8")}</span><span>{t("NotificationPushSettings.46124e0236")}</span><span>{t("NotificationPushSettings.731ce7ed80")}</span><span></span></div>
          {notificationEvents.map(event => {
            const soundEvent = event as SoundEvent
            return <div class="alert-event-row" key={event}>
              <strong>{eventLabels[event]}</strong>
              <Dropdown
                ariaLabel={t("NotificationPushSettings.6bf83b9d4f", { value1: eventLabels[event] })}
                disabled={soundMuted}
                value={sounds.events[soundEvent] ? sounds.eventSounds[soundEvent] : 'off'}
                onChange={value => chooseSound(soundEvent, value)}
                options={[{ value: 'off', get label() { return t("NotificationPushSettings.ca7981b46e") } }, ...soundOptions.map(sound => ({ value: sound.id, label: sound.label }))]}
              />
              <input aria-label={t("NotificationPushSettings.75451b5eb8", { value1: eventLabels[event] })} type="checkbox" disabled={pushMuted} checked={push.events[event]} onChange={changeEvent => changePush({ ...push, events: { ...push.events, [event]: changeEvent.currentTarget.checked } })} />
              <button type="button" disabled={soundMuted || !sounds.events[soundEvent]} aria-label={t("NotificationPushSettings.9aaa421670", { value1: eventLabels[event] })} onClick={() => preview(sounds, sounds.eventSounds[soundEvent])}>{t("NotificationPushSettings.324b134f57")}</button>
            </div>
          })}
        </div>
      </div>
    </div>

    <div class="alert-subsection">
      <strong>{t("NotificationPushSettings.da91b11835")}</strong>
      <p>{t("NotificationPushSettings.d218afd76c")}</p>
      <div class="sound-preset-grid" role="group" aria-label={t("NotificationPushSettings.1d71116329")}>
        {soundOptions.map(sound => <button type="button" key={sound.id} aria-label={t("NotificationPushSettings.7b5f33f587", { label: sound.label })} onClick={() => preview(sounds, sound.id)}><span aria-hidden="true">{sound.glyph}</span><strong>{sound.label}</strong><small>{sound.description}</small></button>)}
      </div>
      <label>{rich("NotificationPushSettings.755ca3ba5e", { value1: <input type="file" accept="audio/*" onChange={event => { const file = event.currentTarget.files?.[0]; if (file) void safeSoundFile(file).then(customSound => { const next = { ...sounds, customSound }; changeSounds(next); preview(next, 'custom') }).catch(cause => setSoundError(cause.message)) }} /> })}</label>
      {sounds.customSound && <div class="theme-actions"><button type="button" onClick={removeCustom}>{t("NotificationPushSettings.7c5f1bdff9")}</button></div>}
      <p aria-live="polite">{rich("NotificationPushSettings.11ff16c4ce", { lastReason: lastReason })}</p>
      {soundError && <p class="settings-inline-error">{soundError}</p>}
    </div>

    <div class="alert-subsection alert-browser-status">
      <div><strong>{t("NotificationPushSettings.36f168bad1")}</strong><p>{supported ? (subscribed ? t("NotificationPushSettings.b9f49ce2c5") : t("NotificationPushSettings.8497aa07d7")) : t("NotificationPushSettings.a06ef3828e")}</p></div>
      {supported && (subscribed ? <button type="button" disabled={busy} onClick={unsubscribe}>{t("NotificationPushSettings.4c9cf06a55")}</button> : <button type="button" disabled={busy} onClick={subscribe}>{t("NotificationPushSettings.0378e09139")}</button>)}
      {permission === 'denied' && <p class="settings-inline-error">{t("NotificationPushSettings.c83478e833")}</p>}
      {status && <p aria-live="polite">{status}</p>}
    </div>
  </section>
}

// Compatibility for code importing the former channel-specific component.
export const NotificationPushSettings = NotificationAlertSettings
