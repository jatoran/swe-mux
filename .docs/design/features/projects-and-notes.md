# Projects, groups, and resources

## What it is

Canonical folder ownership for sessions, layouts, the Project note, and browsed/edited
files. The Projects manager owns the configured-project catalog; optional Groups and
per-Project sidebar visibility control active navigation without changing ownership.

## Ownership model

- A Project is the only session container. It has a stable ID, user-controlled name, and
  one canonical existing folder.
- The Projects manager lists configured Projects independently of the sidebar. Adding,
  renaming, grouping, opening, hiding/showing, and deleting Projects happens there. Hiding
  a Project removes it from desktop/mobile active navigation and numeric Project shortcuts,
  but preserves its configuration, note, files, history, sessions, and layout.
- Creating a Project explicitly initializes `<root>/.swe-mux/config.toml` and
  `<root>/.swe-mux/notes/project.md`.
- Every spawned session has one immutable `project_id` and starts at that Project's root.
  Later `cd` commands change runtime telemetry, never ownership, notes, layout, or defaults.
- A Group is optional Project organization: name, order, and project membership only. It
  never owns a layout, session, note, root, or behavior.
- Git project scopes and repository groups remain derived metadata for status/history. They
  are distinct from the user-created Project and cannot retarget it.

## Project resources

- Each Project has exactly one project note at `.swe-mux/notes/project.md`.
- The project file browser is a lazily expanded tree rooted at the canonical folder.
  Traversal and symlink escapes are rejected; each directory result is bounded to 2,000
  entries. Each file/folder context menu can reveal the resource in the host file manager,
  add its basename to global ignores, or add its Project-relative path to Project ignores.
  Windows reveal selects files and brings the resulting Explorer window forward.
- Global `project_ignore_patterns` and project-local `ignore_patterns` compose. Patterns
  filter the tree and resource watcher only; they never alter Git. Defaults omit dependency,
  environment, cache, build, workspace, and lock artifacts. Settings preserves line breaks
  while either list is edited; Save trims entries and removes blank lines before persistence.
- UTF-8 files up to 2 MiB open as their own revision-checked editor tabs. Binary and larger
  files remain visible but read-only.
- Project note, file browser, file editors, terminals, and previews use the same persisted
  tab/pane tree. Any resource can share a pane with another view, move between panes, or create
  a split at a pane edge. Every resource title row can close its tab; closing never deletes
  the resource.
- Project notes autosave after a short idle debounce. Session note actions resolve to the
  owning Project note, so they share the same autosave contract. Note/file editors insert a
  literal tab character for `Tab`; text files retain an explicit revision-safe Save action.
  Note soft-wraps retain each line's leading indentation, and `Enter` carries that leading
  whitespace onto the new line.
- The controlled CodeMirror note editor creates and reconciles its document in layout
  effects. Moving prop synchronization to a passive effect can replay a stale pre-keystroke
  value after browser input, causing rapid overwrite/re-render loops and an apparent crash.
- Open tree directories and parents of open file tabs renew short watcher leases. The daemon
  watches only those directories, non-recursively, and emits coalesced changes. Collapsed,
  closed, expired, and ignored directories consume no watcher.
- Project and workspace-tab order is persisted. Project positions are normalized in one SQLite
  transaction behind an optimistic whole-order contract; whole-row dragging and Move up/down
  commands share that contract. Project rows and resource/session-stack tabs are directly
  draggable without separate handles. Every Project Group and tab strip is a continuous reorder
  surface: gaps, nested content, spacers, edges, and the source position map to the nearest valid
  insertion slot. Dragging shows a labeled ghost and live insertion preview;
  the native source remains mounted while CSS visual order shifts siblings, and explicit
  before/after markers identify the pending insertion. Drop persists the latest synchronous
  preview; pane-edge hover previews a split, and Escape cancels without persisting. Mixed tab
  order and pane membership remain part of the optimistic Project layout. Reparenting a file
  editor or Files tab preserves its unsaved draft or expanded-tree state.
- `.swe-mux/config.toml` is versioned and permits only `default_shell_profile`,
  `preferred_backend`, `prompt_library_scope`,
  `notification_sounds_enabled`, and additive `ignore_patterns`. Database Project overrides
  take precedence, then portable Project values, then global defaults; an explicit spawn request
  takes precedence where applicable. Commands, executables, network settings, hooks, automatic
  actions, credentials, and secrets are rejected.

## Layout and deletion

- A Project owns one optimistic-revisioned layout for all view kinds. Closing a live or
  ended terminal tab uses the inline two-click kill/remove confirmation; closing a resource
  view never deletes its file.
- A Project cannot be deleted while live or historical sessions still reference it.
- Deleting a Group ungroups its Projects and changes nothing else.

## Key files

- Project manager: `src/swe_mux/projects.py`
- Project and Group persistence: `src/swe_mux/history.py`
- Project files/config/note: `src/swe_mux/project_files.py`
- Leased resource watcher: `src/swe_mux/project_watcher.py`
- Resource editor/browser: `frontend/src/ProjectResource.tsx`
- Host file-manager launch: `src/swe_mux/file_manager.py`
- Indentation-aware note editor: `frontend/src/ProjectNoteEditor.tsx`
- Sidebar and workspace: `frontend/src/App.tsx`
- Projects manager: `frontend/src/ProjectsManager.tsx`
- Drag preview/order helpers: `frontend/src/dragReorder.ts`
