# Session and reset notifications

## What it is

Optional device-local sounds for normalized root-session attention/completion and confirmed
unexpected provider-quota resets. Sounds supplement, but never replace, the durable inbox.

## Contract

Optional browser-device sounds consume the same normalized event stream as the notification
inbox. Supported reasons are root turn complete, ready/waiting, approval or Q&A attention,
failure, and confirmed unexpected quota reset. Reset sounds are emitted only after the durable
quota classifier's timer, drop-size, floor, independent-confirmation, and account/auth gates all
pass. EventBus semantic deduplication collapses hook and transcript duplicates; the browser adds
a short sequence-aware guard. Events explicitly scoped to subagents/sidechains are rejected.
The account popover can persistently classify a Codex alert as manual usage or discard any alert
as a detection error. Reviewed evidence remains in telemetry history but leaves the active alert
summary; review cannot retract a sound already emitted for the original confirmed event.

Preferences live only in browser localStorage: master enable, volume, quiet hours (including
overnight ranges), per-event mute, a selected bundled preset, or an audio file no larger than
512 KiB stored as a data URL. Seven curated 0.5-second presets are copied from the MIT-licensed
Orca reference and retain its license beside the assets. Selecting any preset previews it;
Two Tone is the default. Previewing unlocks/validates browser playback. No arbitrary script or
shell hook runs, and existing inbox/toast delivery remains independent. A portable Project may
disable sounds for its own events, but cannot enable a device whose master setting is off.
The daemon exposes the packaged files at `/notification-sounds`; this route must remain separate
from the SPA fallback so preview clicks receive audio rather than `index.html`.

## Key files

- `frontend/src/sessionSounds.ts`
- `frontend/src/NotificationSoundSettings.tsx`
- `frontend/src/ProviderAccounts.tsx`
- `frontend/public/notification-sounds/`
