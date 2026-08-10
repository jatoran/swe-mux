# Session and reset alerts

## What it is

Alerts are normalized root-session events delivered through an optional sound channel in a live tab and an optional Web Push channel that can reach a locked phone.
Supported events cover attention, completion, waiting, failure, and confirmed unexpected provider-quota resets.
Both delivery channels supplement, but never replace, the durable inbox.

## Contract

Optional browser-device sounds consume the same normalized event stream as the notification
inbox. Supported reasons are root turn complete, ready/waiting, approval or Q&A attention,
failure, and confirmed unexpected quota reset. Reset sounds are emitted only after the durable
quota classifier's timer, drop-size, floor, independent-confirmation, and account/auth gates all
pass. EventBus semantic deduplication collapses hook and transcript duplicates; the browser adds
a short sequence-aware guard. Events explicitly scoped to subagents/sidechains are rejected.

`turn_aborted` is a cancellation, not a failure.
It covers deliberate session shutdown, user interruption, and provider clear/rollback paths, so neither sound nor push may classify it as `failure`.
Unexpected process death remains visible through `session_crashed`, and an explicit agent failure remains visible through `turn_failed`.

Three rules decide whether an idle session is worth interrupting a human for, and all three
apply identically to sounds and to web push (`classify_notification` and
`classifySoundEvent` are mirrors; the tests pin them):

- **Running work suppresses it.** A turn end carrying `idle_reason: waiting_on_background`,
  or a `subagents`/`background_tasks` standing annotation, means the turn ended and the
  agent did not — it resumes itself when that work lands, so this is not the moment worth
  interrupting for. The one after it is, and suppressing here keeps "the chime means the
  agent is done" true instead of training the user to ignore it. Scheduled engagements
  (`loop`, `cron`) do **not** suppress: ready means ready.
- **A session settling after startup is not ready for you.** `state_changed` with
  `previous: starting` never notifies. It is inferred from PTY quiet ~1s after spawn, it is
  not even input-ready (the CLI swallows the submitting CR for seconds after it), and
  nothing about it means the agent wants something the human asked for.
- **"Ready" is held before it is believed.** See below.

The first two read fields that must be present on `state_changed`, not only on
`turn_ended` — see `features/status-detection.md` § What `state_changed` carries.

The account popover can persistently classify a Codex alert as manual usage or discard any alert
as a detection error. Reviewed evidence remains in telemetry history but leaves the active alert
summary; review cannot retract a sound already emitted for the original confirmed event.

## Settling the "ready" alert

`waiting` ("the agent is ready for you") is raised by a *state transition*, and a transition
into `idle` is a claim about the future that the next two minutes can falsify: a turn-end
notify lands mid-turn, the PTY watchdog reads an idle prompt during a pause, a Codex
`agent-turn-complete` arrives before the model is done. Nothing at the moment of the idle
distinguishes those from a real turn end — only what happens next does. So the category is
held for `WAITING_SETTLE_SECONDS` (120 s) before any routing decision, and the agent
resuming cancels it, reusing the same cancellation the deferral path already had.

120 s is measured, not chosen: over one 10-hour, 17-session day, 89 of 211 idle transitions
were back to `working` inside that window with no human input in between. The 8 s
semantic-dedup window caught none of them, because the flaps run 11-50 s. Categories that
are true the instant they are raised (`failure`, `reset`, questions, and elicitation) are never held.
Approval attention has its own 5 s stabilization before `approval_needed` exists.
The same stabilized event drives sidebar state, foreground sound, automation attention, and web push, so an auto-approved review produces none of them.
An approval a Codex auto reviewer is answering is held further, until the CLI actually draws the dialog or a 60 s ceiling passes, because nothing else distinguishes it from an approval the user is being asked (see `status-detection.md` → Approval stabilization).
This is separate from the 120 s waiting settle and from device deferral.

A settle and a deferral are different questions and compose: the settle asks "did the agent
actually stop", the deferral asks "is the human somewhere else", so a held-then-deferred
ready alert can arrive up to ~165 s after the idle. Anything that resolves a session
(human input, the agent resuming) cancels both — `cancel_pending`, not just the deferral
map, or a settled alert outlives the resume that falsified it.

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

What counts as "at that device" is defined once, in `features/device-presence.md`: fresh
heartbeat, visible, focused, and interacted with inside the activity window — deliberately
stricter than focus, and failing open to absent. Push consumes that verdict; it does not
define it.

Being active elsewhere *defers* the alerts worth chasing (`attention`, `waiting`) rather than
dropping them. Plain suppression assumes the user stays put; they don't — they get up
mid-turn, and the notification they most needed is the one it eats. A deferred push waits ~45s
and then delivers, unless the user interacted with the other device after the alert was
raised (they were there and chose not to act), or the session was dealt with in the meantime —
human input into it, or the agent resuming, cancels anything held for it. Enablement, category
and quiet hours are re-checked when the deferral fires, since it can cross into quiet hours.
Categories that go stale while held (`complete`, `failure`, `reset`) are dropped, not deferred.
`session_exited` and `session_crashed` also cancel every pending settle and deferral for that session before any terminal failure alert is considered.

## Preferences

Every alert preference is stored on the daemon under a Desktop or Mobile device-class profile so either device can configure both profiles and the push sender can enforce policy without a live browser.
The `alerts` domain owns the profile-wide master and one quiet-hours schedule, including overnight ranges.
The `sounds` and `notifications` domains remain independent delivery-channel policies under that master.
Muting the master suppresses sound and push without changing either channel's enabled state or any per-event choice.
The sidebar bell controls this shared master and must be labelled as Alerts rather than claiming to control only notifications.

The Settings surface has one device-profile selector, one master, two channel toggles, and one event matrix.
Each event row selects a sound or `Off` and independently enables push.
Browser push subscription and permission are capability state for the current physical browser, not profile policy, so they are displayed separately from the Mobile/Desktop push channel toggle.
The durable inbox remains available while alert delivery is muted.

Profiles created before the `alerts` domain derive the master from either legacy channel being enabled.
Legacy push quiet hours win while push is enabled; otherwise enabled sound quiet hours are preserved.
The first unified-policy edit persists an explicit `alerts` domain without rewriting the legacy channel blobs or losing custom audio and event choices.

The shared sound library contains seven bundled presets plus one optional audio file no larger than 512 KiB stored as a data URL.
Uploading a custom sound adds `Custom` to the same preview and event-selection surfaces; it does not reassign events.
Removing it resets only events assigned to `Custom` back to Two Tone.
Legacy sound preferences with one global selection migrate that choice to every event.
The seven curated 0.5-second presets are copied from the MIT-licensed Orca reference and retain its license beside the assets.
Library clicks and event-row Preview actions play without changing other event assignments; selecting from an event dropdown assigns and previews that event's choice.
Two Tone is the default.
Previewing unlocks and validates browser playback.
No arbitrary script or shell hook runs, and existing inbox/toast delivery remains independent.
A portable Project may disable sounds for its own events, but cannot enable a device whose shared master or sound channel is off.
The daemon exposes the packaged files at `/notification-sounds`; this route must remain separate from the SPA fallback so preview clicks receive audio rather than `index.html`.

## Key files

- `frontend/src/sessionSounds.ts`
- `frontend/src/alertPrefs.ts`, `frontend/src/notificationPrefs.ts`
- `frontend/src/NotificationPushSettings.tsx` (the unified Alerts settings surface)
- `frontend/src/push.ts` (subscription lifecycle; presence itself lives in
  `features/device-presence.md`)
- `frontend/src/ProviderAccounts.tsx`
- `frontend/public/notification-sounds/`
- `src/swe_mux/push.py`, `src/swe_mux/settings_store.py`

## Relates to

- `features/device-presence.md` — supplies "is the user somewhere else", and the
  "did they touch it since" question a deferred push turns on.
