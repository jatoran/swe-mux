# Projects and Groups

## What it is

Projects are the durable catalog of canonical folders swe-mux can own. The Projects manager
owns that catalog; the sidebar is a filtered active-navigation view of it. Optional Groups and
persisted ordering organize Project rows without acquiring behavioral ownership.

## Ownership model

- A Project has a stable ID, user-controlled name, one canonical existing folder, optional
  Group, persisted position, and `sidebar_visible` state.
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
- The sidebar renders only visible Projects, ordered by Group and normalized Project position.
  Whole-row pointer dragging and Move up/down actions use the same persisted reorder contract.
- Creating a Project validates the root and initializes `.swe-mux/config.toml` plus
  `.swe-mux/notes/project.md`. The registration is not inferred from Git or current cwd.
- A Project cannot be deleted while live or historical sessions reference it. Deleting a Group
  ungroups its Projects and changes nothing else.

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
PATCH|DELETE /api/project-groups/{group_id}
GET|PUT /api/project/config?cwd=<root>
```

The registry reads and writes both layers directly: `PATCH /api/projects/{id}` for the database
override (`default_backend`, `default_profile_id`, sent as `null` to clear) and revision-checked
`PUT /api/project/config` for the portable file.

Project layout writes are revision checked. Whole-order reorder writes are validated as a
complete permutation of every registered Project ID and normalized transactionally.

## Key files

- `src/swe_mux/projects.py`
- `src/swe_mux/history.py`
- `src/swe_mux/project_files.py`
- `frontend/src/ProjectsManager.tsx`
- `frontend/src/App.tsx`
- `src/swe_mux/project_actions.py`

## Relates to

- `project-resources.md`: Project-owned notes, files, ignores, and watches.
- `workspace-layout.md`: Project-owned pane/tab placement.
- `sessions.md`: immutable Project ownership and spawning.
- `project-actions.md`: explicit trusted repository task execution.
