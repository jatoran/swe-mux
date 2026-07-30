# Frontend package responsibilities

## Composition boundary

`frontend/src/App.tsx` is the browser composition root. It owns top-level daemon snapshots,
selected Project/view, modal/menu routing, and cross-feature commands. Leaf components own their
rendering and local interaction; pure helpers own transformations that need deterministic tests.

## Package map

| Area | Primary files | Boundary |
|---|---|---|
| Workspace composition | `App.tsx` | fetch/coordinate Projects, sessions, layouts, menus, overlays |
| Layout algebra | `layout.ts` | parse/migrate and pure stack/split/leaf transforms |
| Mobile projection | `mobileWorkspace.ts` | pure flatten/select/adjacent-close rules; no persistence |
  | Terminal viewport | `TerminalPane.tsx`, `terminalProtocol.ts`, `terminalViewport.ts`, `terminalRenderer.ts`, `terminalRenderDiagnostics.ts` | xterm/WS lifecycle, pre-replay attach sizing, renderer policy/fallback (DOM-only for mobile and Codex), replay, device-response classification/Codex late-color suppression, input, responsive fitting, jump-to-latest tail convergence and which backends are also asked to move their own viewport, dev-only render diagnostics |
| Multi-device terminal input | `inputOwnership.ts`, `terminalLetterbox.ts` | pure client half of the daemon's arbitration: epoch-ordered ownership frames, gesture-vs-passive claim classification (the device *class* on a claim comes from `currentProfile()`, the same one the presence heartbeat reports, because the daemon compares them), the visible-and-focused gate on re-claiming (displacement only, never a refusal, and never twice inside the cooldown), what the take-over strip is allowed to speak for, one-shot replay of refused keystrokes, and the font-size math for rendering a size another device chose. `TerminalPane.tsx` owns the socket, the DOM, and the take-over strip |
| Project resources | `ProjectResource.tsx`, `ProjectNoteEditor.tsx`, `noteSaveQueue.ts`, `noteEditorSettings.ts`, `fileClipboard.ts` | file tree/editors, note-specific save isolation, the pure config → editor-configuration resolution (element props vs `--continuity-*` properties, chord-overlay sanitizing), and the pure path-joining/clipboard-truncation rules behind the tree's copy actions |
| Send a selection to an agent | `noteSelection.ts`, `agentTargets.ts`, `SendToAgentPicker.tsx` | pure Continuity-snapshot slicing (UTF-8 byte offsets → UTF-16), message composition, bracketed-paste payload rules, and which sessions may receive a send; the dialog stages/asks the queue to deliver (and owns the explicit "send anyway" retry), `App.tsx` owns spawn/composer-fill/placement |
| Prompt queue | `queueApi.ts`, `QueuePane.tsx` | typed client over `/api/queue/*` with the refusal→outcome mapping (`mapQueueSendError`), head-of-line/pending selectors, schedule/sender helpers (`scheduleStatus`, `senderLabel`), and the per-target Queue tab (list, arm/edit/reorder/cancel/skip, send-now + confirm, stranded retarget, composer, the auto-delivery strip, per-item schedule presets); the daemon owns every safety decision, `App.tsx` owns tab placement, the pane chip, and `mux:queue-changed` re-dispatch |
| Mailbox | `Mailbox.tsx` | app-level overlay over `/api/queue/mailbox` and `/api/queue/auto`: inbox/outbox with sender/target provenance and delivery state, per-item revoke, pause-all auto-delivery, "report unsafe delivery", and the proving-period counters. Shows delivery state only — never a conversation |
| History | `HistoryBrowser.tsx` | filters, transcript review, backfill progress/actions |
| Projects | `ProjectsManager.tsx` | configured catalog UI plus the single per-Project settings editor (both storage layers), not workspace placement |
| Project actions | `ProjectRunMenu.tsx` | Run catalog, trust, and launch interaction |
| Preview links/views | `previewLinks.ts`, `PreviewPane.tsx`, `TerminalPane.tsx` | loopback normalization, link dispatch, sandboxed registered viewport |
| Accounts/resources | `ProviderAccounts.tsx`, `ResourceUsage.tsx`, `resourceTotals.ts`, `resourceTooling.ts` | anchored viewport popovers and summaries; the rail shows working set from the shared poll, while the open popover fetches `?unique_memory=1` on its own timer because that sample is far too costly for a background poll; `resourceTooling.ts` classifies language servers so per-session duplication is named rather than hidden among identical `node.exe` rows |
| Settings | `Settings.tsx`, `settingsDraft.ts`, `settingsSearch.ts` | global options only — per-Project options belong to `ProjectsManager.tsx`; explicit draft/save/discard lifecycle; search indexes the vnode tree of every tab (`Settings.tsx` renders each tab through one id-taking function so unmounted tabs can be built without effects), keeping the index self-maintaining rather than a second list to update |
| Guided onboarding | `GuidedTutorial.tsx`, `tutorial.ts` | action gates, coach-mark geometry, product-event matching, and device-local completion |
| Voice conversation | `ConversationControl.tsx`, `conversation.ts`, `VoicePlayer.tsx`, `voice.ts`, `mobileVoice.ts` | one-device capture ownership, VAD/WAV encoding, wake commands, playback queue/barge-in, direct private-HTTPS redirect |
| Automation/usage/processes | feature-named panels | feature-local navigation and presentation |
| Utility drawer | `UtilityDrawer.tsx`, `drawerTabs.ts`, `ClipboardPanel.tsx`, `CommandsTab.tsx`, `PromptsTab.tsx`, `NotesTab.tsx`, `GitTab.tsx`, `Notifications.tsx` | one host, two renderings (mobile overlay / desktop docked column + icon rail); tab registry, width/tab persistence, and per-tab bodies. Tab bodies own their own data and never place the host. `DRAWER_TABS` is the single registration point: the strip, the icon rail, the `drawer.<id>` commands, and order normalization all derive from it, so a new tab costs a registry row, an icon in `DRAWER_TAB_ICONS`, and a branch in the host — never an `App.tsx` edit per surface |
| Git tab | `GitTab.tsx`, `gitWorktrees.ts` | reads `/api/git/worktrees` and joins it by path to the branch/dirty/upstream state `git_monitor.py` mirrors into session snapshots; `gitWorktrees.ts` holds the pure half (porcelain parsing, separator/case-tolerant boundary-matched path comparison, branch-row aggregation, absolute-path validation). Mutations are limited to the `worktree add`/`remove` the API wraps (`design/features/git.md`) |
| Clipboard capture | `clipboardHistory.ts`, `insertTarget.ts` | boot-installed copy capture (writeText wrapper + capture-phase copy/cut) with client-side dedupe, and pure last-focused-surface insert routing shared by every injecting surface |
| Shared interaction | `dragReorder.ts`, `pointerDragClaim.ts`, `menuPosition.ts`, `modalFocus.ts`, `keys.ts`, `sidebarProjects.ts`, `sessionAttention.ts`, `wheelScroll.ts`, `MenuGroup.tsx` | pure or narrowly stateful reusable behavior, including `wheelScroll.ts`'s wheel-to-horizontal translation for tab strips, which overflow only sideways (the terminal action rail solves the same problem with its own inline handler and could adopt this), including collapsed-Project labels, live/read aggregation, and the collapsible menu group (desktop flyout / touch accordion) any `.context-menu` can host. `pointerDragClaim.ts` is the one deliberately process-wide piece: a running drag claims the pointer there so the mobile gesture recognizer stands down, which is arbitration between two window-level listeners and has nowhere else to live |
| Notifications | `push.ts`, `devicePresence.ts`, `notificationPrefs.ts`, `NotificationPushSettings.tsx` | service-worker/subscription lifecycle, the presence heartbeat this device reports over `App.tsx`'s `/events` socket (interaction as an age, never a timestamp), the per-device-class focus rule `deviceIsFocused` that terminal claims share, and the per-profile preference shape mirroring `settings_store.py`. Every delivery decision is the daemon's: the browser reports what it can see and nothing more |
| Connection liveness | `liveness.ts`, `api.ts` | one recovery policy (attempt deadlines, resume signals, backoff) for every long-lived socket and load, plus the `fetch` wrapper's request timeout |

## Extraction rule

Put a transformation in a pure helper when the same state has three or more branches, must be
tested without a browser, or is used by desktop and mobile. Keep network orchestration in
`App.tsx` only when it coordinates multiple features.

Correct:

```ts
const projection = mobileWorkspaceProjection(layout, focusedViewId, activeTerminalId)
```

Incorrect:

```ts
// A responsive component must not mutate persisted desktop geometry while rendering.
if (mobile) updateLayout(projectId, flattenIntoOneStack(layout))
```

## UI state boundaries

- Daemon snapshots are refreshed/coalesced at the composition root.
- Project layout is optimistic durable state; focus, sidebar size/collapse, audio unlock, and
  responsive mode are device-local state.
- A never-arranged Project opens on the empty stage. There is no first-open seeding: the two
  things worth seeding a pane with (Files, the Project note) are now one drawer tab and one
  click away, so seeding would only cost pixels and a layout write.
- `layouts.py` stores `note:`/`file:` resource IDs opaquely — they are a browser encoding — with
  one exception it must know about: a `files:` leaf from layout v6 is pruned rather than stored,
  in both `layout.ts` and `layouts.py`, because no pane can render one any more.
- File/note drafts remain with their resource components/queues and survive view reparenting.
- Popovers that can escape a narrow sidebar use viewport portals. Modal focus/backdrop behavior
  is centralized rather than reimplemented per dialog.
- Conversation capture is browser-volatile and pinned to one session. Audio chunks go only to
  that session's transcription route; wake-command parsing and pending draft stay client-side,
  while muxd owns idempotent submission and the human-input transition.
- Tutorial completion is a versioned localStorage preference. Coach-mark navigation opens
  existing Projects/Accounts/Run surfaces; successful Project, account, spawn, and pointer-drop
  operations emit UI-only progress events. Those events acknowledge work but never become a
  second Project, account, session, layout, or Settings state authority.
- High-frequency pointer movement updates refs and one DOM indicator, not component state. See
  `workspace-state.md`.
- Nothing that reaches the daemon may end in a state only a remount can leave. A resumed PWA can
  hang a WebSocket handshake or a `fetch` indefinitely without erroring, so `liveness.ts` owns one
  policy for all of it: every attempt carries a deadline (`HANDSHAKE_TIMEOUT_MS` for sockets,
  `REQUEST_TIMEOUT_MS` passed to `api`), failures back off, and `watchResume`/`watchLiveness`
  re-check on visibility, `pageshow`, `online`, focus, and a visible-only poll. `shouldForceReconnect`
  is the single pure decision (stalled handshake, backoff due, or an attempt older than a
  suspension long enough to have killed it silently), so the `/events` socket (`App.tsx`), each
  `/pty` socket (`TerminalPane.tsx`), resource loads (`ProjectResource.tsx`), and autosave retries
  (`noteSaveQueue.ts`) all recover the same way instead of each inventing a rule. A resume burst
  (visibility + focus + online together) collapses to one attempt; a failure surfaced to the user
  always offers an immediate retry.

## Related design

- `../../design/features/ui.md`
- `../../design/features/workspace-layout.md`
- `../../design/features/project-resources.md`
- `../../design/features/project-actions.md`
