# Device presence

## What it is

Which device class the human is actually using right now, tracked per client connection
and aggregated to `desktop` / `mobile`. Two independent subsystems need the same answer
and neither can derive it from its own state: notification routing
(`features/notifications.md`) and terminal input arbitration
(`features/terminal-input.md`).

## Key concepts

- ACTIVE: heartbeat fresh, window visible **and** focused, and a real interaction within
  the activity window. All four, not any.
- LEADING: the active device class whose last interaction is the most recent. Breaks the
  routine both-active tie.
- INTERACTION: a pointer or key event anywhere in the client document, reported as an
  *age* in seconds — never a timestamp, so a phone's clock skew cannot make it look
  permanently present.
- FOCUSED: per device class. Mobile reports visibility alone; desktop requires
  `document.hasFocus()`.

## Operations

- Report: clients send `{type:"presence", profile, visible, focused, interaction_age}` on
  the `/events` socket every 30 s, on every visibility/focus change, and on the first
  interaction after 10 s of quiet. The connection closing drops the device.
- Aggregate: any active connection makes its class active; `leading_profile` picks the
  class with the newest interaction.
- Ask: `interaction_since(moment, exclude=profile)` answers "was the user demonstrably at
  another device while this alert was pending" for a deferred push.

## Constraints + trade-offs

- Focus alone is not presence. A desktop left open and focused is indistinguishable from
  one being typed into, and treating it as presence silences the device the user carried
  away. Requiring a recent interaction is what makes the signal mean anything.
- The activity window (120 s) is generous, so both classes are routinely active at once —
  precisely when someone picks up their phone. LEADING exists because that tie has to
  resolve toward the hands, not toward the incumbent.
- Presence rides `/events` rather than the push-presence endpoint because every client
  holds that socket whether or not it can receive Web Push. The Windows desktop shell is a
  WebView that cannot subscribe, so it reported nothing at all through the push path — any
  cross-device rule built on that would ship and do nothing.
- Device class comes from `currentProfile()` on every client surface that reports or
  claims. The daemon compares those strings; a surface using a different breakpoint would
  report itself in use under one name and be judged under another.

## Failure modes

- Stale heartbeat (frozen tab, dropped socket) ⇒ TTL 90 s ⇒ device treated as absent.
- Half-reported device (visible, never interacted) ⇒ not active ⇒ cannot lead.
- No presence at all (old client, socket down) ⇒ no leader ⇒ consumers fall back to their
  own per-session or per-subscription rules.

Every path fails open — absent, never present. A redundant notification and a redundant
takeover prompt are cheap; a silenced approval and a stolen keyboard are not.

## Interfaces

- `POST /events` frame `presence` (`design/interfaces.md`).
- `GET /api/push/presence` — current view: per device visible, focused, interaction age,
  heartbeat age, active; plus `active_profiles` and `leading_profile`.

## Configuration

- `ACTIVITY_WINDOW_SECONDS` (120), `HEARTBEAT_TTL_SECONDS` (90) in
  `src/swe_mux/device_presence.py`. Not user-configurable.

## Key files

- Store and rules: `src/swe_mux/device_presence.py`
- Socket intake and diagnostic endpoint: `src/swe_mux/server.py` (`events_ws`,
  `get_device_presence`)
- Client heartbeat and per-class focus rule: `frontend/src/devicePresence.ts`
- Heartbeat wiring: `frontend/src/App.tsx` (events socket effect)

## Relates to

- `features/notifications.md` — routes and defers push using ACTIVE and
  `interaction_since`.
- `features/terminal-input.md` — arbitrates passive input claims using LEADING.
