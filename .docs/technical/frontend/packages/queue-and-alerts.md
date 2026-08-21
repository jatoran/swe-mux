# Frontend: queues, schedules, approvals, and alerts

Index: `../packages.md`.
Design: `../../../design/features/prompt-queue.md`, `../../../design/features/scheduled-runs.md`, `../../../design/features/approvals.md`, `../../../design/features/attention-ranking.md`.

## Prompt queue and fleet queue

`queueApi.ts`, `QueuePane.tsx`, `FleetQueue.tsx`

Neither surface is a conversation, and the daemon owns every safety decision.

`QueuePane` is strictly session-scoped: ordered list, arm/edit/reorder/cancel/skip/delete, send-now confirmation, stranded retarget, composer, conversation auto-delivery disclosure, and schedule presets.
Its drawer rendering follows focus, and its `queue:` pane leaf pins a target.
Delete uses an inline second-click confirmation, applies to every non-delivering visible state, and is also available from `FleetQueue`.
`QueuePane` is the only surface carrying the **install-wide** brakes - pause-all, report-unsafe, proving counters - because it is the only queue surface that delivers and the one a person is already looking at when they decide delivery must stop.
Its `auto:` strip is also where a grant explains itself, for the same reason: a lapsed grant states the numbers behind the lapse (idle for how long, under what window, how many messages left waiting) and a grant held open by a live exchange says so, since neither is inferable from "off" or "on".
The idle window itself is a *value* rather than a switch, so the lapse notice links to Settings through `queue.grantWindow` and never offers a grant - `GrantGate` remains reserved for the install master (`../../../design/features/setting-links.md`).

`FleetQueue` is application-scoped and a **modal**: explicit authorship partition (opening on non-human), daemon-side Project and session filters, cross-target provenance and delivery state, revoke, and delete.
It delivers nothing and owns no control; it reports install-wide auto-delivery state and hands off to a target's Queue.

`queueApi.ts` owns typed clients, refusal-to-outcome mapping, head and pending selectors, deletion, and schedule and sender helpers.
Its `fetchFleetQueue` calls `/api/queue/mailbox`, whose name predates the surface's.

`App.tsx` owns Queue drawer and pop-out placement, `openFleetQueue`, `toggleAutoPaused` (the `autodelivery.pause` command that needs nothing open), fleet pending totals, and `mux:queue-changed` re-dispatch.

## Scheduled runs

`ScheduleTab.tsx`, `schedules.ts`

The drawer's Project-scoped Schedule tab, with its own Project/all-Projects scope owned by `App.tsx` like the Processes one.
It draws the inventory (cadence, countdown, last verdict), pause and resume, Run now, delete, and an expandable prompt and run history.
Its full-column editor replaces the list rather than opening beside it, because at drawer width a form and a list side by side leaves neither usable.

`schedules.ts` owns the types and the pure presentation: cadence and duration wording, the countdown, ordering (armed and soonest first), draft/body conversion, and `needsAttention`, which counts failures and armed-but-blocked schedules while deliberately excluding a deliberate pause.
`CRON_PRESETS`/`presetForCron` are the named expressions beside the cron field, matched back from the field's own text so an edited one reads as Custom, each carrying the piece of the grammar it demonstrates.

A *resume* schedule is never authored from a blank form here.
It is seeded (`resumeDraft`) by the History row's "Resume later…" or a pane's own menu, because the tab has no way to find a conversation and a run-id box would be a worse picker than the two that exist.
The editor swaps the harness and overlap controls for the target kind (each option stating its own cost through `TARGET_KIND_COPY`), the rolling-continuation ceiling, and - for a pinned fork - the cut-point list read from `GET /api/history/{id}/branch-points`.
`actionLabel` and `targetIsMissing` carry what a row does and whether its conversation still exists.

Three things the surface never does:

- It never computes a fire time.
  Cron plus timezone plus DST has one implementation, in the daemon, and the editor previews through `POST /api/schedules/preview`.
- It never recomputes whether a cut point is legal.
  The daemon has read the transcript; a browser copy would drift and arm a fork that fails nightly.
- It never renders a schedule that cannot fire as if it can.
  `blocked` and a missing resume target are both live per-request answers drawn on the row.

`frontend/test/renderer/schedule-layout.spec.ts` pins the geometry, because the drawer column is the narrowest surface in the app.

## Control-plane approvals

`approvals.ts`, `ApprovalChip.tsx`

`approvals.ts` is the browser-free reading of the approval axis: mode labels and descriptions, `approvalLapse` (why a stored grant is not in force - `expired`, `superseded`, `exhausted`), `effectiveApprovalMode`, the summary line, `approvalChipLabel`, and `modeUnavailableReason`.
The effective mode is recomputed client-side rather than taken from the endpoint's `effective_mode` **for the row badge specifically**, because the badge renders from the ordinary session snapshot every `update` frame already carries and must not need a request per session.
The two implementations apply the same expiry and run-id checks, and `approvals.test.ts` pins the cases where they could drift.

`ApprovalChip.tsx` is the pane bar's one standing chip; the per-session `tts:` control it used to sit beside moved into the voice panel's `tts` tab (`../../../design/features/voice.md`).
It is rendered for every agent pane *including* ones where no mode can be selected: a control that vanishes when unavailable teaches the operator it does not exist, while one that stays and states its reason teaches them what would make it work.
`approvalChipLabel` is capped at four characters and pinned there by test, because the chip shares a bar with the session name, the path, and the tools; the numbers, expiry, and refusal reasons live in its drop-down, which registers with the dismiss stack like every other level and closes on an outside pointer-down.
It does not cycle on click, because `allow_all` is not a step on the way back to `wait`, so each mode is chosen directly.

The chip owns no policy: every refusal is the daemon's, and a rejected click re-reads rather than leaving the menu showing a choice the server declined.
The one-shot Approve lives on the command rail (`TerminalPane.tsx`) and routes through the daemon rather than writing `\r`, because only the server can re-check the agent run, the screen classification, and the prompt fingerprint.

## Attention ranking

`attention.ts`, `AttentionInbox.tsx`, the head of `Notifications.tsx`

`attention.ts` is the JSX-free half: the daemon shapes, the fixed channel order, the per-channel copy, the suppression-reason vocabulary, and the fan-out and budget headline builders, so the ordering and labelling rules are testable under the node runner.
`AttentionInbox.tsx` renders channels as separate groups rather than one score-sorted list, because merging cheap-blocking with expensive-blocking work is the failure mode the whole feature exists to avoid.
It always draws the suppressed count, the budget line, and any mined rule awaiting an explicit accept or reject.
Narration is drawn as an aside under the deterministic summary, never in place of it, so a narration failure reads as a missing aside.
It leads the Alerts tab and holds no push or subscription path of its own.

## Alerts

`alertPrefs.ts`, `sessionSounds.ts`, `push.ts`, `devicePresence.ts`, `notificationPrefs.ts`,
`NotificationPushSettings.tsx`

`alertPrefs.ts` owns the per-device-class master and shared quiet hours; sound and push remain delivery channels under it.
`NotificationPushSettings.tsx` is the unified policy surface and separates the current browser's subscription capability from Desktop and Mobile profile choices.
The presence heartbeat reports interaction age over `App.tsx`'s `/events` socket, and the daemon makes every push delivery decision from the same effective policy the foreground sound path applies.
