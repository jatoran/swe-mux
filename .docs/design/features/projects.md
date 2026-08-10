# Projects and Groups

## What it is

Projects are the durable catalog of canonical folders swe-mux can own. The Projects manager
owns that catalog; the sidebar is a filtered active-navigation view of it. Optional Groups and
persisted ordering organize Project rows without acquiring behavioral ownership.

## Ownership model

- A Project has a stable ID, user-controlled name, one canonical existing folder, optional
  Group, persisted position, registration date, and `sidebar_visible` state.
- A Project is the only container for sessions, layout, notes, file resources, Project options,
  and Project-scoped history work. A session's `project_id` is immutable.
- Every spawn defaults to the canonical Project root.
  An explicit spawn may use a contained subdirectory or an exact Git-listed worktree root of the same repository.
  Later `cd` commands update runtime/Git telemetry only and never retarget ownership.
- A Group owns a name, order, and Project membership only. It never owns a folder, layout,
  session, note, setting, or behavior.
- Git scopes, repository IDs, and repository groups are derived metadata. They cannot replace
  or retarget the explicit Project.

## Project registry and sidebar

- The Projects manager lists every configured Project and is the only catalog-management UI.
  It can add, rename, group, open, show/hide, configure, and delete registrations.
- It is also the **only per-Project editor**. Its detail pane has two tabs: `Details` (name,
  folder, group, sidebar visibility, delete) and `Settings` (default backend, shell profile,
  prompt-library scope, notification sounds, additive ignore patterns). Settings holds global
  options exclusively and has no per-Project section; `Project settings…` on a Project's context
  menu opens this registry preselected to that Project's Settings tab (`project.settings`).
  Splitting these across two modals meant three doors to two overlapping surfaces, one of which
  bounced the user back out to the other.
- Both storage layers are edited in one form. A dual-layer field (backend, shell profile) shows
  one control plus a `this device` / `repo` selector saying where the value is stored; writing a
  value always clears the other layer, since a stale database override would silently outrank
  the value just chosen. Repo-only fields carry a static `repo` chip. Users pick a value and a
  home for it, never a layer to hunt through.
- Hiding a Project removes it from desktop/mobile navigation and numeric Project shortcuts. It
  preserves the registration, `.swe-mux/` content, layout, history, settings, and live sessions.
- The sidebar renders only visible Projects.
  Ungrouped Projects are root rows immediately under the global `PROJECTS` header; explicit Groups render afterward as named sections.
  `PROJECTS` is navigation chrome, not a synthetic Group, and cannot fold or move among Groups.
- The `PROJECTS` header carries a sort control (`⇅`) covering **two levels**.
  Flat options act on root Projects and Projects inside every Group: Manual order, Recently used, Name A→Z / Z→A, Newest / Oldest first.
  A `Sort Groups` submenu acts only on explicit Groups: Manual order, Recently used, Name A→Z / Z→A.
  Both live on one control because `⇅` already means "how is this list ordered"; the submenu keeps the common case one click deep and carries its current mode in its label, since Group order has no always-visible indicator of its own.
- Project sorting is **one global mode**, applied to root Projects and inside every Group.
  It was per section originally, on the argument that a hand-arranged shortlist and a long alphabetical pile are both legitimate; that put a `⇅` on every Group header for a preference set the same everywhere, so the modes collapsed into one and the control moved off the headers.
  A device upgrading from the per-section format keeps whichever mode it had actually set (see `loadSidebarOrder`), rather than being silently reset to Manual.
  Group sorting is necessarily one setting.
  Both sort-mode selections are device-local presentation preferences.
- The same header carries `⊟`/`⊞`, which folds or unfolds **every Project row and every Group**
  at once, so tidying a long sidebar is one click rather than one per row. It offers Expand only
  once nothing on screen is folded open; expanding clears the stored fold lists outright rather
  than subtracting the visible ids, so an id left behind by something hidden or deleted while
  folded cannot survive and re-fold it later. Only what is on screen is folded — a Project hidden
  from the sidebar has no row to collapse.
- Groups have no date modes: a Group record is not dated, and "newest Group first" does not earn
  a column. A Group's "Recently used" rank is the most recent explicit user action for
  any Project in it, so a Group ranks on the work the user initiated rather than its age; an
  empty Group reads as unmeasured and sorts last.
- Manual order is the default and the tie-break at both levels, so a sort never discards the
  arrangement underneath it.
  Placing something by hand returns *that level* to Manual and writes the order that was on screen, so the move survives instead of being re-sorted away.
  Desktop pointer dragging and Project-menu Move up/down use the same persisted reorder contract.
  Mobile Project rows require a 325 ms hold before pickup; movement beyond the 8 px hold slop remains sidebar scrolling and never previews a reorder.
- Desktop Group headers combine collapse and reorder: press and move reorders, while press and release folds.
  Mobile Group headers only fold because Project rows are the sidebar's sole mobile reorder target.
  The desktop drag swallows the click it ends with.
  Collapsing is presentation only: the folded Projects keep their place in the collapsed rail, numbered shortcuts, and every order.
- Mobile Project pickup closes open menus, gives short haptic feedback, previews one insertion inside the current root list or Group, and edge-scrolls the tree until release.
  Releasing commits one order write; cancellation preserves the original order.
  Project Group assignment remains an explicit Project-menu or registry action rather than a cross-Group drop side effect.
- Mobile Project rows expose `⋮` immediately left of Run for the Project context menu.
  Project long-press is reserved for reorder; desktop right-click remains the pointer context-menu route.
  Mobile session long-press remains context-menu-only and never starts sidebar grouping or reorder.
- A Group header's only button is `✎` (rename). It carried a `×` that deleted the Group and
  ungrouped its Projects; that sat a pixel from the fold toggle and dissolved a Group on a stray
  click, so the sidebar no longer deletes Groups at all. Emptying one has the same visible effect,
  since a Group with no Projects in it is not rendered as a section.
- A folded Group reports both a live-session count and the strongest agent state inside it,
  in the collapsed rail's colours. A count alone would let an agent waiting for approval
  disappear behind the fold, which is the one thing collapsing must not hide.
- A drag only ever permutes the rows on screen; hidden Projects and empty Groups keep the slots
  they already held rather than being reshuffled by a reorder the user could not see.
- "Recently used" reads the daemon-persisted `ProjectRecord.last_used_at` timestamp, so mobile and desktop rank the same explicit Project use.
  The sort-mode selection remains device-local; selecting Recently used on one browser does not change another browser's selected mode.
  A successful explicit prompt submission or user-initiated session start advances the timestamp through `POST /api/projects/{project_id}/used`.
  The daemon emits `project_used` with the resulting timestamp so every connected client updates without a fleet refetch.
  Opening or focusing a Project, session, note, file, preview, Queue, or other resource never changes it.
  Agent output, state transitions, session completion, session removal, history timestamps, and background automation never change it.
  A Project without a recorded action is unmeasured and retains manual-order tie-breaking.
  The timestamp survives browser storage loss, a daemon restart, and a desktop redeploy.
  Existing databases seed the new field from the latest non-imported session start because no older exact prompt-submit record exists.
- Device-local Group fold state is pruned against the registered Groups, so a Group id that is deleted and later reused cannot inherit a fold the user never applied.
  The prune is suppressed until the Group registry has actually loaded, for the same reason.
- Creating a Project validates the root and initializes `.swe-mux/config.toml` plus
  `.swe-mux/notes/project.md`. The registration is not inferred from Git or current cwd.
- Add project has two modes of one form: register a folder that exists, or create a new folder
  inside an existing parent. Create mode makes exactly one directory, so a mistyped deep path is
  an error rather than a silently materialized tree, and the duplicate-root and group checks run
  before anything is created. Two dialogs were rejected because each would need its own copy of
  the setup-command list below.
- A Project cannot be deleted while live or historical sessions reference it. Deleting a Group
  ungroups its Projects and changes nothing else.

## Project setup commands

Add project offers the user's own setup commands as checkboxes, unchecked unless their
definition opts in. They are defined in Settings → General, stored in the machine-local daemon
config (`project_init_scripts`: `id`, `label`, `command`, `default_enabled`), and never read
from a folder. There is no built-in scaffolding matrix — git, a workspace file, a virtualenv,
and anything else are whatever the user writes in a command.

Each selected command becomes one ordinary one-shot shell terminal owned by the new Project and
rooted at its canonical folder, started in configured order. Start order is all that is
promised: a step that must finish before the next begins belongs in the same command, using the
shell's own `&&` or `;`. The registration is already durable when they run, so a command that
fails to launch is reported and never unwinds the Project.

This is not an exception to the ordinary repository-configuration boundary. A Project Action is imported
from the checkout and therefore needs exact-content approval; a setup command is typed by the
user into their own settings, so authoring it is the authorization. Execution reuses the Project
Action spawn contract, and nothing here reads repository content.

## Configuration boundary

Project-owned `.swe-mux/config.toml` is versioned and permits typed portable options:
`default_shell_profile`, `preferred_backend`, `prompt_library_scope`,
`notification_sounds_enabled`, additive `ignore_patterns`, and the narrow `[worktree].setup_command` launch hook.
Effective precedence is explicit
request where supported, database Project override, portable Project value, then global default.

Repository-owned `.swe-mux/config.toml` cannot authorize general commands, executables, hooks, network bindings, automatic actions, credentials, or secrets.
The sole command exception is `[worktree].setup_command`, which runs only after an explicit user create-and-spawn worktree request and before that worktree's session starts.
The Projects manager exposes it under Git and worktrees and states that it is committed executable configuration.
Blank uses an executable `.worktree-setup` convention instead.
Separately, `.vscode/tasks.json`, root
`package.json` scripts, and `.swe-mux/actions.toml` may execute only after the user selects a
Project Action and locally approves the exact current task-file fingerprint. They never execute
on discovery, Project open, or daemon startup.

`git_compare_ref` is a nullable machine-local Project-record field.
It controls Git review display comparisons and never belongs in `.swe-mux/config.toml`, because changing a review base must not dirty the repository.
`null` means Auto inference; a non-empty bounded string is an explicit ref that the Git review domain validates and resolves before use.
Resetting the Git selector to Auto persists `null` through the ordinary Project PATCH path.

## API shape

```text
GET|POST /api/projects
PATCH|DELETE /api/projects/{project_id}
PUT /api/projects/order
GET|POST /api/project-groups
PUT /api/project-groups/order
PATCH|DELETE /api/project-groups/{group_id}
GET|PUT /api/project/config?cwd=<root>
```

The registry reads and writes both layers directly: `PATCH /api/projects/{id}` for database
overrides (`default_backend`, `default_profile_id`, `git_compare_ref`, sent as `null` to clear) and revision-checked
`PUT /api/project/config` for the portable file.

Project layout writes are revision checked. Whole-order reorder writes are validated as a
complete permutation of every registered Project ID and normalized transactionally. Group
reordering takes the same contract, including the `expected_order` guard that answers `409
order_conflict` when a second device already moved something — two devices each writing a full
permutation would otherwise let the loser silently win.

Project payloads carry `created_at` (registration), daemon-persisted `last_used_at` (explicit user use), and derived `last_activity` (latest session activity from history), all as epoch seconds with `0` when unknown.
`last_activity` is derived on read rather than stored, because history already dates every
session and a second write path could only drift from it.

## Key files

- `src/swe_mux/projects.py`
- `src/swe_mux/history.py`
- `src/swe_mux/project_files.py`
- `src/swe_mux/project_init.py`
- `frontend/src/ProjectsManager.tsx`
- `frontend/src/projectCreate.ts`
- `frontend/src/projectSort.ts`
- `frontend/src/App.tsx`
- `src/swe_mux/project_actions.py`

## Relates to

- `project-resources.md`: Project-owned notes, files, ignores, and watches.
- `workspace-layout.md`: Project-owned pane/tab placement.
- `sessions.md`: immutable Project ownership and spawning.
- `project-actions.md`: explicit trusted repository task execution.
