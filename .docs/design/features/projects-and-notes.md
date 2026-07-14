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
  note pane. The Projects shelf includes a separate **App-owned space notes** section, so a
  note from a deleted space remains reachable as an archived space note.
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
  Markdown editor may be a modal or a persistent split leaf; docking changes layout only.
- The footer status light exposes storage destination, save state, and revision on hover.
  Paths are not shown in the editor chrome. External changes yield a visible conflict.
- Projects is the durable shelf for project-local config and notes. App-owned space notes
  are visibly separated from project-owned items. Legacy space-note files discovered under
  project `.swe-mux/` are labelled backups, not active notes.
- Docked notes appear beside their semantic owner in the sidebar: space notes directly
  under the space; agent-run and project notes under their associated session. Unmatched
  durable notes remain visible as unattached rows instead of disappearing.
- Saved app-owned space notes appear beneath their space whether or not they are docked.
  The row distinguishes `saved` from `open pane`; selecting a saved note opens its editor,
  while selecting an open-pane note focuses that existing viewport on narrow screens.
- Selecting a terminal always selects a terminal viewport. A resource-only persisted layout
  is expanded to terminal + resource when its live shell is selected; a note can never trap
  navigation or replace session identity.

## Key files

- Project identity: `src/swe_mux/projects.py`
- Project config/notes: `src/swe_mux/project_files.py`
- App-owned space notes and migration: `src/swe_mux/app_notes.py`,
  `src/swe_mux/note_migration.py`
- Scope/artifact registry: `src/swe_mux/history.py`
- Notes UI/routing: `frontend/src/Notes.tsx`, `frontend/src/App.tsx`
- Durable shelf: `frontend/src/ProjectRegistry.tsx`
