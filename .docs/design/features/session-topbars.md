# Configurable session top bars

## What it is

Each live terminal pane has a session top bar above its terminal surface.
Settings → Appearance → Session top bars controls its metrics, shortcuts, alignment, density, and one-to-three-row layout.
The same layout is used on desktop and mobile.

## Layout model

`SessionTopbarConfig` is stored in the browser-owned `sessionTopbar` device-settings domain under the canonical `desktop` profile.
The daemon stores the document opaquely.

- A layout contains one to three rows.
- Every row has ordered left and right sections plus a separator.
- A metric or shortcut occurs at most once in the whole layout.
- Title is required and cannot be removed.
- The overflow session menu is fixed outside the configurable catalog.
- Removing a row rehomes its items instead of discarding them.
- Normalization repairs malformed items, duplicate entries, invalid separators, absent title, and excess rows.

The shipped one-row default preserves the existing agent controls: title and conditional cwd on the left, approvals, Queue, and Transcript on the right.

## Metrics and shortcuts

Metrics reuse `ROW_FIELDS` and `sessionFieldToken` from the sidebar session-row system.
The two surfaces therefore share field vocabulary, notability, duration semantics, Git attribution, model labels, and token styles.
Each placed metric is `when notable` or `always`.

Shortcuts are approvals plus every entry in `DRAWER_TABS`.
Drawer shortcuts retain their registered label and scope.
Queue and Transcript remain visible but disabled when the named session cannot use them, so configured placement does not silently collapse.

## Rendering and geometry

`SessionTopbar` renders configured rows at intrinsic height and gives the terminal surface the remaining pane height.
Changing the persistent row count deliberately changes PTY geometry once; ordinary state changes never add or remove rows.
The shared five-second row clock updates time-based metrics inside the top bar without making `App.tsx` a clock subscriber.

Density is `compact`, `standard`, or `comfortable`.
The title keeps its bounded yielding width and faults remain visible beside it.
The overflow menu stays at the first row's right edge on every layout.

## Settings and navigation

The editor has its own Appearance subpage and a sticky live preview on desktop and mobile.
The preview renders one hypothetical active session and updates from local editor state before persistence finishes.
Right-clicking a pane top bar and choosing **Configure appearance** deep-links to this page.
The same row from sidebar, tab, and mobile session menus continues to target Appearance → Session rows.

## Key files

- Model and catalog: `frontend/src/sessionTopbarConfig.ts`
- Persistence: `frontend/src/sessionTopbarPrefs.ts`, `src/swe_mux/settings_store.py`
- Live renderer: `frontend/src/SessionTopbar.tsx`, `frontend/src/App.tsx`
- Editor and preview: `frontend/src/SessionTopbarSettings.tsx`
- Shared metrics: `frontend/src/sessionRowFields.ts`, `frontend/src/SessionRowBody.tsx`
- Geometry and appearance: `frontend/src/style.css`
- Tests: `frontend/test/sessionTopbarConfig.test.ts`, `frontend/test/renderer/pane-layout.spec.ts`, `frontend/test/renderer/settings-layout.spec.ts`
