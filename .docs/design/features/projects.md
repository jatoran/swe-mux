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
- Every spawn begins at the canonical Project root. Later `cd` commands update runtime/Git
  telemetry only and never retarget ownership.
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
- The sidebar renders only visible Projects, grouped into sections: one per Group, plus the
  ungrouped remainder labelled `PROJECTS`. The remainder is a section like any other — it
  sorts, reorders, and folds on the same terms, and orders by its visible name.
- Each section header carries a sort control (`⇅`) covering **two levels**. Flat, and acting on
  that section's Projects: Manual order, Recently active, Name A→Z / Z→A, Newest / Oldest first.
  Behind a `Sort Groups` group, acting on the sections themselves: Manual order, Recently
  active, Name A→Z / Z→A. Both live on the header because a header's `⇅` already means "how is
  this list ordered" and the sidebar has no section-level header to hang a second control on;
  the submenu keeps the common case one click deep and carries its current mode in its label,
  since section order has no always-visible indicator of its own. The submenu is named for
  Groups, the app's own word for these, even though it also orders the ungrouped remainder —
  its tooltip carries that, and "sections" was jargon nothing else in the UI used.
- Project sorting is per section because Groups are how unlike things are separated — a
  hand-arranged shortlist and a long alphabetical pile are both legitimate, and one global mode
  cannot be both. Section sorting is necessarily one setting. Both are device-local.
- Sections have no date modes: neither a Group record nor the synthetic remainder is dated, and
  "newest Group first" does not earn a column. A section's "Recently active" is the latest
  activity of any Project in it, so a Group ranks on the work inside it rather than its age; an
  empty Group reads as unmeasured and sorts last.
- Manual order is the default and the tie-break at both levels, so a sort never discards the
  arrangement underneath it. Placing something by hand returns *that level* to Manual and writes
  the order that was on screen, so the move survives instead of being re-sorted away: dragging a
  Project row (or Move up/down, the same persisted reorder contract) for Projects, dragging a
  section header for sections.
- A section header is also its collapse toggle — press and move to reorder, press and release to
  fold, disambiguated by the drag swallowing the click it ends with, exactly as a Project row
  resolves drag-versus-select. Collapsing is presentation only: the folded Projects keep their
  place in the collapsed rail, the numbered shortcuts, and every order.
- A folded section reports both a live-session count and the strongest agent state inside it,
  in the collapsed rail's colours. A count alone would let an agent waiting for approval
  disappear behind the fold, which is the one thing collapsing must not hide.
- A drag only ever permutes the rows on screen; hidden Projects and empty Groups keep the slots
  they already held rather than being reshuffled by a reorder the user could not see.
- "Recently active" ranks on the latest session activity a Project has ever had, derived from
  history (so Projects with nothing live still rank) and merged with live sessions at minute
  granularity (so a busy PTY cannot re-sort the sidebar out from under the pointer). A Project
  that has never run a session, or one registered before registrations were dated, reads as
  unknown and sorts last in either direction rather than posing as the oldest.
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

This is not an exception to the repository-configuration boundary. A Project Action is imported
from the checkout and therefore needs exact-content approval; a setup command is typed by the
user into their own settings, so authoring it is the authorization. Execution reuses the Project
Action spawn contract, and nothing here reads repository content.

## Configuration boundary

Project-owned `.swe-mux/config.toml` is versioned and permits only typed portable options:
`default_shell_profile`, `preferred_backend`, `prompt_library_scope`,
`notification_sounds_enabled`, and additive `ignore_patterns`. Effective precedence is explicit
request where supported, database Project override, portable Project value, then global default.

Repository-owned `.swe-mux/config.toml` cannot authorize commands, executables, hooks, network
bindings, automatic actions, credentials, or secrets. Separately, `.vscode/tasks.json`, root
`package.json` scripts, and `.swe-mux/actions.toml` may execute only after the user selects a
Project Action and locally approves the exact current task-file fingerprint. They never execute
on discovery, Project open, or daemon startup.

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

The registry reads and writes both layers directly: `PATCH /api/projects/{id}` for the database
override (`default_backend`, `default_profile_id`, sent as `null` to clear) and revision-checked
`PUT /api/project/config` for the portable file.

Project layout writes are revision checked. Whole-order reorder writes are validated as a
complete permutation of every registered Project ID and normalized transactionally. Group
reordering takes the same contract, including the `expected_order` guard that answers `409
order_conflict` when a second device already moved something — two devices each writing a full
permutation would otherwise let the loser silently win.

Project payloads carry `created_at` (registration, epoch seconds; `0` when unknown) and derived
`last_activity` (latest session activity from history; `0` when the Project has never run one).
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
