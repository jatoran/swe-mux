# Configurable session top bars

## What it is

Each live terminal pane has a session top bar above its terminal surface.
Settings → Appearance → Session top bars controls its metrics, shortcuts, alignment, density, and one-to-three-row layout.
Desktop defines the default layout; mobile inherits it until its first separate edit.

## Layout model

`SessionTopbarConfig` is stored in the browser-owned `sessionTopbar` device-settings domain under the `desktop` and `mobile` profiles.
The daemon stores the document opaquely.
An absent or empty mobile domain inherits the current desktop configuration, including future desktop edits.
The first mobile edit writes a complete independent configuration, preserving the inherited items, styles, density, and rows except for that edit.
An explicit mobile configuration remains independent even when it happens to equal desktop.
Existing desktop configurations need no migration and remain the default for every device without a mobile override.
The live renderer selects the interaction profile from `deviceMode.ts`, independently of viewport width, and updates on settings or device-mode changes.

- A layout contains one to three rows.
- Every row has ordered left and right sections plus a separator.
- A metric or shortcut occurs at most once in the whole layout.
- Every item is removable, the title included; a bar with nothing placed is a legal layout.
- The overflow session menu is fixed outside the configurable catalog, which is what keeps every pane's recovery path whatever is removed.
- Removing a row rehomes its items instead of discarding them.
- Normalization repairs malformed items, duplicate entries, invalid separators, and excess rows.

The stored document carries a `version`, and the title's removability is what it records.
Under version 1 the editor could not remove the title, so a stored layout without one could only be malformed and normalization put it back at the head of the first row.
Version 2 made the title removable, so a version-2 layout without a title is a choice and is kept.
A blob carrying version 1, or no version at all, still receives the repair; every write from a current build stamps the current version.

The shipped one-row default preserves the existing agent controls: title and conditional cwd on the left, approvals, Queue, and Transcript on the right.

## Metrics and shortcuts

Metrics reuse `ROW_FIELDS` and `sessionFieldToken` from the sidebar session-row system.
The two surfaces therefore share field vocabulary, notability, duration semantics, Git attribution, model labels, and token styles.
Each placed metric is `when notable` or `always`.

Two placed metrics additionally carry their own rendering (`style`), each from its own vocabulary.
`context` chooses among `percent`, `gauge`, and `both`: the sidebar draws context on its state indicator by default, and a top bar has no indicator, so a placed `context` used to inherit `arc` and draw nothing.
`sessionTopbarRowConfig` resolves it to an in-row rendering: its own `style`, else the sidebar's setting when that already draws in the row, else a percentage.
`cwd` chooses among `leaf`, `relative`, and `full` (the sidebar's `cwdStyle` vocabulary) and follows the sidebar's setting when the item names none, because a folder name reads well in a narrow sidebar row and a path reads well in a wide pane header.
Every other metric renders under the sidebar's configuration untouched, so the two surfaces cannot disagree about a diff, a count, or a ramp.
A stored `style` on any other field, or one outside its field's vocabulary, is dropped by normalization.

The preview session is a registered harness (`codex`), not a placeholder name: the context gauge is gated on the harness's `measurement` capability, so a made-up backend previewed a bar whose placed context drew nothing while the real one drew fine.

Shortcuts are approvals plus every entry in `DRAWER_TABS`.
Drawer shortcuts retain their registered label and scope.
Queue and Transcript remain visible but disabled when the named session cannot use them, so configured placement does not silently collapse.

## Rendering and geometry

`SessionTopbar` renders configured rows at intrinsic height and gives the terminal surface the remaining pane height.
Changing the persistent row count deliberately changes PTY geometry once; ordinary state changes never add or remove rows.
The shared five-second row clock updates time-based metrics inside the top bar without making `App.tsx` a clock subscriber.

Density is `compact`, `standard`, or `comfortable`.
The title keeps its bounded yielding width.

The session-fault marker is not a metric and cannot be removed.
It is drawn beside the title while the title is placed, and alone at the head of the first row when the layout has no title, because a stale transcript is the one fault that otherwise looks like a healthy session and the agent header has no other pane-level surface for it.
The overflow menu stays at the first row's right edge on every layout.

## Settings and navigation

The editor has its own Appearance subpage and a sticky live preview on desktop and mobile.
The Desktop/Mobile switch selects which profile to edit and initially selects the current device's profile.
Switching profiles only reads settings and never creates an override.
Mobile reports whether it inherits desktop or uses its own configuration.
**Use desktop layout** clears the mobile domain to `{}` and resumes inheritance.
**Reset to default** writes the shipped default into the selected profile; on mobile this remains an independent layout.
The preview fills the current device's available Settings width and has no separate width control.
The preview renders one hypothetical active session and updates from local editor state before persistence finishes.
A removed title is offered again under the row's add controls, so the removal is reversible from the same editor.
Right-clicking a pane top bar and choosing **Configure appearance** deep-links to this page.
The same row from sidebar, tab, and mobile session menus continues to target Appearance → Session rows.

## Diagnostics

`mux.session-topbar-diagnostics.v1` retains the latest 64 browser-local save-started, save-completed, and save-failed records.
Each record carries an ISO timestamp, severity, component, profile, page identifier, operation sequence, inheritance state, row count, and density without session content.
Failed saves remain visible in the editor and produce console warnings.

## Key files

- Model, catalog, and the per-item context rendering: `frontend/src/sessionTopbarConfig.ts`
- Persistence: `frontend/src/sessionTopbarPrefs.ts`, `src/swe_mux/settings_store.py`
- Live renderer: `frontend/src/SessionTopbar.tsx`, `frontend/src/App.tsx`
- Editor and preview: `frontend/src/SessionTopbarSettings.tsx`
- Shared metrics: `frontend/src/sessionRowFields.ts`, `frontend/src/SessionRowBody.tsx`
- Geometry and appearance: `frontend/src/style.css`
- Tests: `frontend/test/sessionTopbarConfig.test.ts`, `frontend/test/renderer/pane-layout.spec.ts`, `frontend/test/renderer/settings-layout.spec.ts`, `frontend/test/renderer/session-topbar-profiles.spec.ts`
