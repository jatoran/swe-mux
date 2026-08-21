# Frontend package responsibilities

Where browser code lives and how to modify it.
The map is split by domain so a change touches one file; this page is the index and the shared rules.

## Composition boundary

`frontend/src/App.tsx` is the browser composition root.
It owns top-level daemon snapshots, selected Project/view, modal/menu routing, and cross-feature commands.
Leaf components own their rendering and local interaction.
Pure helpers own transformations that need deterministic tests.

## Extraction rule

Put a transformation in a pure helper when the same state has three or more branches, must be tested without a browser, or is used by desktop and mobile.
Keep network orchestration in `App.tsx` only when it coordinates multiple features.

Correct:

```ts
const projection = mobileWorkspaceProjection(layout, focusedViewId, activeTerminalId)
```

Incorrect:

```ts
// A responsive component must not mutate persisted desktop geometry while rendering.
if (mobile) updateLayout(projectId, flattenIntoOneStack(layout))
```

The node test runner strips types from `.ts` but cannot load `.tsx`.
That is why several pure models are split out of the component that renders them (`dotShapes.ts`, `coldSession.ts`, `settingsTabs.ts`, `modelRouting.ts`).

## Domain maps

- [`packages/composition.md`](packages/composition.md) - workspace composition, UI state boundaries, connection liveness, server clock, redeploy progress, shared interaction primitives.
- [`packages/terminal.md`](packages/terminal.md) - the terminal viewport, multi-device input arbitration, mobile keyboard and IME, preview links, ended and recovered panes.
- [`packages/layout-and-chrome.md`](packages/layout-and-chrome.md) - layout algebra, the mobile projection, the utility drawer and its segments, overflow rails, the pinned rail and its overflow popover, theme, scale and rail density, icon sets.
- [`packages/sidebar-and-projects.md`](packages/sidebar-and-projects.md) - session rows, the sidebar filter, the Projects registry, Project actions, session display names.
- [`packages/notes-and-files.md`](packages/notes-and-files.md) - Project resources, the note editor host, and sending a selection to an agent.
- [`packages/actions-and-clipboard.md`](packages/actions-and-clipboard.md) - the Action rail and its editors, prompt templates, agent skills, clipboard capture and its surfaces.
- [`packages/queue-and-alerts.md`](packages/queue-and-alerts.md) - the prompt queue, the Fleet Queue, scheduled runs, control-plane approvals, attention ranking, alerts.
- [`packages/git-and-history.md`](packages/git-and-history.md) - the Git tab and landing, History, work lineage, branch points, the transcript reader.
- [`packages/agent-inspection.md`](packages/agent-inspection.md) - the Agent tab's Instructions/Config/Tools segments and the Activity tab's Timeline/Findings/Change Map segments.
- [`packages/metering.md`](packages/metering.md) - the Resources dialog and its four segments, usage analytics, automation spend, provider accounts.
- [`packages/settings-and-gates.md`](packages/settings-and-gates.md) - Settings, setting links and grant gates, budget controls, model selection, guided onboarding.
- [`packages/voice-and-assistant.md`](packages/voice-and-assistant.md) - voice capture and playback, and the Mux assistant.

## UI state boundaries

- Daemon snapshots are refreshed and coalesced at the composition root and merged through `sessionSnapshots.ts`.
- Snapshot ordering is `(daemon generation, session revision)`, not arrival order; a new generation resets the revision domain.
- Project layout is optimistic durable state.
  Focus, sidebar size/collapse, audio unlock, and responsive mode are device-local.
- **Persisted device-local state may not be reconciled against a daemon snapshot until that snapshot has loaded.**
  The composition root mounts holding empty arrays for every collection it fetches, so an effect that drops "state for records that no longer exist" runs first against a registry that appears to hold nothing, and its write-back persists the deletion.
  Represent not-yet-loaded explicitly (`null`, not `[]`) at the pure helper's boundary so the destructive reading is unavailable by construction rather than avoided by ordering.
  `pruneSidebarOrder` protects sidebar fold state from mount-time empty snapshots.
- Utility-drawer width is one device-local value, with a viewport-derived live cap that preserves 150 px for the main workspace.
  Selected tab and desktop expansion are device-local values keyed by Project.
  Mobile overlay visibility and in-progress resize collapse previews are transient and cannot mutate the desktop expansion map.
- A never-arranged Project opens on the empty stage, with no first-open seeding: Files and Notes are each one drawer tab and one click away, so seeding would only cost pixels and a layout write.
- `layouts.py` stores `note:`/`file:` resource IDs opaquely, because they are a browser encoding.
  The one exception it must know about is a `files:` leaf from layout v6, pruned rather than stored in both `layout.ts` and `layouts.py`, because no pane can render one.
- File and note drafts remain with their resource components or queues and survive view reparenting.
- Popovers that can escape a narrow sidebar use viewport portals.
  Modal focus and backdrop behavior is centralized rather than reimplemented per dialog.
- Conversation capture, its draft, following target, and optional exact-sink pin are browser-volatile workspace state.
  Audio uses the session-free transcription route because decoding does not choose a destination.
  Focus chooses among live agents and connected named text surfaces, while wake-command parsing and the pending draft stay client-side.
  Agent append and submit are acknowledged by the mounted `TerminalPane`, which keeps xterm, PTY ownership, replay buffering, and the human-input transition on the ordinary terminal path.
- Tutorial completion is a versioned localStorage preference.
  Coach-mark navigation opens existing Projects/Accounts/Run surfaces, and successful Project, account, spawn, and pointer-drop operations emit UI-only progress events.
  Those events acknowledge work but never become a second Project, account, session, layout, or Settings authority.
- High-frequency pointer movement updates refs and one DOM indicator, not component state (`workspace-state.md`).

## Related design

- `../../design/features/ui.md`
- `../../design/features/workspace-layout.md`
- `../../design/features/project-resources.md`
- `../../design/features/project-actions.md`
