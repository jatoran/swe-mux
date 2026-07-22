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
| Terminal viewport | `TerminalPane.tsx`, `terminalProtocol.ts`, `terminalViewport.ts`, `terminalRenderer.ts`, `terminalRenderDiagnostics.ts` | xterm/WS lifecycle, pre-replay attach sizing, renderer policy/fallback, replay, input, responsive fitting, dev-only render diagnostics |
| Project resources | `ProjectResource.tsx`, `ProjectNoteEditor.tsx`, `noteSaveQueue.ts` | file tree/editors and note-specific save isolation |
| History | `HistoryBrowser.tsx` | filters, transcript review, backfill progress/actions |
| Projects | `ProjectsManager.tsx` | configured catalog UI, not workspace placement |
| Project actions | `ProjectRunMenu.tsx` | Run catalog, trust, and launch interaction |
| Preview links/views | `previewLinks.ts`, `PreviewPane.tsx`, `TerminalPane.tsx` | loopback normalization, link dispatch, sandboxed registered viewport |
| Accounts/resources | `ProviderAccounts.tsx`, `ResourceUsage.tsx` | anchored viewport popovers and summaries |
| Settings | `Settings.tsx`, `settingsDraft.ts` | explicit draft/save/discard lifecycle |
| Guided onboarding | `GuidedTutorial.tsx`, `tutorial.ts` | action gates, coach-mark geometry, product-event matching, and device-local completion |
| Voice conversation | `ConversationControl.tsx`, `conversation.ts`, `VoicePlayer.tsx`, `voice.ts`, `mobileVoice.ts` | one-device capture ownership, VAD/WAV encoding, wake commands, playback queue/barge-in, direct private-HTTPS redirect |
| Automation/usage/processes | feature-named panels | feature-local navigation and presentation |
| Shared interaction | `dragReorder.ts`, `menuPosition.ts`, `modalFocus.ts`, `keys.ts`, `sidebarProjects.ts`, `sessionAttention.ts` | pure or narrowly stateful reusable behavior, including collapsed-Project labels and live/read aggregation |

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
- First-open layout seeding lives in `App.tsx` over the pure `defaultProjectLayout` helper, because
  `note:`/`files:` resource IDs are a browser encoding that `layouts.py` only stores opaquely. It
  runs once per Project per session, guarded by revision 0 plus an empty root, and releases its
  guard when the layout PATCH fails.
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

## Related design

- `../../design/features/ui.md`
- `../../design/features/workspace-layout.md`
- `../../design/features/project-resources.md`
- `../../design/features/project-actions.md`
