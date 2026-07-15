# Project configuration and notes

## Ownership model

- A **space** is an app-owned workflow/layout container. It is not a project and has no
  project anchor. A space may contain terminals and agent runs from any number of projects.
- A **project scope** is one normalized concrete worktree/filesystem root. It owns project
  configuration, its canonical project note, and agent-run notes under `.swe-mux/`.
- A **terminal session** is one PTY. A shell can move between projects and has immutable
  spawn identity plus untrusted live-cwd telemetry; it owns no durable session note.
- An **agent run** is one Claude/Codex invocation. Promotion captures immutable run cwd and
  project scope, matching native history/transcript ownership. Each later invocation in the
  same PTY is a separate durable run.
- A repository group is derived display/history metadata only. It never owns behavior,
  configuration, notes, hooks, sessions, or artifacts.

## Spawn and live location

- Space-level New terminal resolves cwd as explicit request → space default → global startup
  cwd → daemon cwd. Project relative `default_cwd` can refine a location only after that
  seed has selected its project scope.
- New tabs and splits send the originating terminal's accepted live cwd explicitly, so they
  open where the user is working. Ordinary space-level creation never inherits an unrelated
  active terminal implicitly.
- Spawn cwd/scope remain trusted and immutable. Validated OSC 7 supplies live cwd for header,
  Git, and explicit current-project convenience. PTY telemetry never grants command, hook,
  trust, proxy, or filesystem authority.
- A user request to open the current project note explicitly resolves/registers the live
  directory before writing. Agent-run notes and run project notes always use immutable run
  scope. Project-scoped global hooks use spawn scope for shells and run scope for agents.

## Project files

- `.swe-mux/config.toml` permits only friendly label, bounded relative default cwd, default
  machine-defined shell profile, and `notes_enabled`. Auth, bind, ports, secrets, hooks,
  executables, and commands are rejected.
- `.swe-mux/notes/project.md` is the project's canonical note.
- A project-note artifact identity equals its owning project-scope identity. Project notes
  never use cross-project legacy discovery. Startup releases any early-build mismatched
  binding and removes only its stale pane reference; the underlying Markdown remains with
  its real project and is never deleted or moved automatically.
- `.swe-mux/notes/sessions/<safe-agent-run-id>.md` stores an agent-run note. History and the
  Projects shelf keep it reachable after the terminal exits.
- Reading or resolving a project creates no files. An explicit save creates project files by
  atomic replacement with revision conflict checks.

## Space notes

- Space notes are app-owned at `<data_dir>/notes/spaces/<safe-space-id>.md`. They do not move
  when terminals `cd`, when membership changes, or when a space default changes.
- The editor header is explicit: `NOTE::SPACE::<space> · APP DATA`. Project and agent-run
  headers name their project owner. The editor has one fixed target; there is no scope toggle
  that can silently route the same text to a different owner.
- Active space notes open from the space menu, main menu, palette, empty stage, or a docked
  note pane. The global Notes shelf indexes live and archived app-owned space notes, so a
  note from a deleted space remains reachable without making it appear project-owned.
- Phase 5.5 project-owned space notes are copied once into app data during daemon startup.
  The source Markdown is retained as a non-canonical legacy backup, the project artifact
  binding is released, and legacy anchor columns are made inert. Migration never deletes or
  overwrites user-authored project files.

## UI rules

- A shell's pane `note` action means **Current project note**. If live cwd is unavailable,
  the trusted spawn/last-known project is used and presented as such.
- Claude/Codex `note` means **Agent-run note**. Menus also expose the immutable run project
  note. Plain shells never create per-PTY notes.
- Space note, project note, and agent-run note are separate explicit commands. The raw
  Markdown editor may be a quick modal or a persistent per-space Notes Dock; presentation
  never changes semantic ownership. Settings chooses the default presentation and defaults
  to Dock.
- The footer status light exposes storage destination, save state, and revision on hover.
  Paths are not shown in the editor chrome. External changes yield a visible conflict.
- Projects is the registry for project roots, project-local config, diagnostics, and file
  recovery. Notes is the durable discovery shelf across saved space, project, and agent-run
  notes, grouped as Recent/Spaces/Projects/Agent runs/Recovered. Entries lead with friendly
  ownership and content metadata rather than filenames or IDs. Legacy space-note files
  discovered under project `.swe-mux/` are labelled backups, not active notes.
- The Notes shelf opens from `# notes` on the right side of the sidebar footer. It searches
  bounded excerpts and owner/project/backend metadata. Recognized notes with durable owners
  open in the canonical editor; unowned recovery files stay visible and read-only until
  their relationship is resolved from Projects. Opening a note from the shelf keeps the
  shelf mounted behind the editor, so `← notes`/`browse` returns to the same query, category,
  and scroll position.
- The Notes Dock belongs to the active space, not a terminal leaf. It stays open while the
  user changes sessions, terminal tabs, or split focus; multiple notes use tabs inside the
  dock. Its width and active note persist with the space. Desktop docks right of the whole
  space; narrow/mobile layouts use the bottom half of the workspace.
- Sidebar rows use only `space note`, `project note`, and `agent note`. A space note is
  directly beneath its space. A live agent note may nest beneath its agent session; project
  notes and ended-run notes remain visible at space level instead of being attached to an
  arbitrary terminal. Saved app-owned space notes appear whether or not the dock is open.
- Selecting a terminal always selects a terminal viewport and never hides or retargets the
  space Notes Dock. Closing a dock tab removes only its presentation; it never deletes the
  note file or durable artifact relationship.

## Key files

- Project identity: `src/swe_mux/projects.py`
- Project config/notes: `src/swe_mux/project_files.py`
- App-owned space notes and migration: `src/swe_mux/app_notes.py`,
  `src/swe_mux/note_migration.py`
- Scope/artifact registry: `src/swe_mux/history.py`
- Notes UI/routing: `frontend/src/Notes.tsx`, `frontend/src/App.tsx`
- Notes discovery shelf: `frontend/src/NotesShelf.tsx`
- Project registry/recovery: `frontend/src/ProjectRegistry.tsx`
