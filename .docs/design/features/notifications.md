# Session and reset notifications

## What it is

Optional sounds in a live tab, and optional Web Push alerts that reach a locked phone,
for normalized root-session attention/completion and confirmed unexpected provider-quota
resets. Both supplement, but never replace, the durable inbox.

## Contract

Optional browser-device sounds consume the same normalized event stream as the notification
inbox. Supported reasons are root turn complete, ready/waiting, approval or Q&A attention,
failure, and confirmed unexpected quota reset. Reset sounds are emitted only after the durable
quota classifier's timer, drop-size, floor, independent-confirmation, and account/auth gates all
pass. EventBus semantic deduplication collapses hook and transcript duplicates; the browser adds
a short sequence-aware guard. Events explicitly scoped to subagents/sidechains are rejected,
and so is a turn end carrying `idle_reason: waiting_on_background`: the agent will resume
itself when its background work lands, so that turn end is not the moment worth interrupting
for. The one after it is — and suppressing here keeps "the completion chime means the agent
is done" true instead of training the user to ignore it. The same rule gates web push.
The account popover can persistently classify a Codex alert as manual usage or discard any alert
as a detection error. Reviewed evidence remains in telemetry history but leaves the active alert
summary; review cannot retract a sound already emitted for the original confirmed event.

## Web push and device routing

Sounds need a live tab, which is dead exactly when an alert matters most — a locked phone.
Web Push is the tab-independent path, and its filtering therefore runs on the daemon, before
any tab exists to filter it. Each subscription records a device-class profile; that profile's
server-stored notification settings decide enablement, per-category opt-in, and quiet hours.

`suppress` decides which presence silences a profile: `never`, `focused` (this device has the
app open and its sound already covers it), or `anyDevice`. `anyDevice` is the mobile default
and also routes around the *other* device, so a phone does not buzz for an approval the user
is watching happen at their desk. It replaces an earlier `suppressWhenFocused` boolean, which
migrates: an explicit `false` becomes `never`; the never-chosen default `true` becomes the
profile's new default.

"The user is at that device" is a stricter test than window focus, because a desktop left
focused while its owner walks away looks identical to one being typed into — and treating
that as presence silences the device they took with them. A device counts as active only
while its heartbeat is fresh, its window is visible **and** focused, and it has had a real
interaction within two minutes. Every staleness path fails open: an unknown, expired or
half-reported device is absent, because a redundant buzz is a far cheaper mistake than a
missed approval. Presence is reported over the `/events` socket (`design/interfaces.md`),
not the push-presence endpoint, because the Windows desktop shell is a WebView that cannot
subscribe to push and so reported nothing at all through that path.

Being active elsewhere *defers* the alerts worth chasing (`attention`, `waiting`) rather than
dropping them. Plain suppression assumes the user stays put; they don't — they get up
mid-turn, and the notification they most needed is the one it eats. A deferred push waits ~45s
and then delivers, unless the user interacted with the other device after the alert was
raised (they were there and chose not to act), or the session was dealt with in the meantime —
human input into it, or the agent resuming, cancels anything held for it. Enablement, category
and quiet hours are re-checked when the deferral fires, since it can cross into quiet hours.
Categories that go stale while held (`complete`, `failure`, `reset`) are dropped, not deferred.

## Preferences

Sound preferences are device-local; notification (push) preferences are stored on the daemon
per device class, because the push sender has to read them with no browser involved. Master
enable, volume, quiet hours (including overnight ranges), per-event mute, and a sound
selection for each event. The shared sound library
contains seven bundled presets plus one optional audio file no larger than 512 KiB stored as a
data URL. Uploading a custom sound adds `Custom` to the same preview and event-selection surfaces;
it does not reassign events. Removing it resets only events assigned to `Custom` back to Two Tone.
Legacy device preferences with one global selection migrate that choice to every event. The seven
curated 0.5-second presets are copied from the MIT-licensed Orca reference and retain its license
beside the assets. Library clicks and event-row Preview actions play without changing other event
assignments; selecting from an event dropdown assigns and previews that event's choice. Two Tone
is the default. Previewing unlocks/validates browser playback. No arbitrary script or shell hook
runs, and existing inbox/toast delivery remains independent. A portable Project may disable
sounds for its own events, but cannot enable a device whose master setting is off.
The daemon exposes the packaged files at `/notification-sounds`; this route must remain separate
from the SPA fallback so preview clicks receive audio rather than `index.html`.

## Key files

- `frontend/src/sessionSounds.ts`
- `frontend/src/NotificationSoundSettings.tsx`
- `frontend/src/NotificationPushSettings.tsx`, `frontend/src/notificationPrefs.ts`
- `frontend/src/push.ts`, `frontend/src/devicePresence.ts`
- `frontend/src/ProviderAccounts.tsx`
- `frontend/public/notification-sounds/`
- `src/swe_mux/push.py`, `src/swe_mux/device_presence.py`, `src/swe_mux/settings_store.py`
