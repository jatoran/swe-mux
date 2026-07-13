# Project configuration and notes

## What it is

- Project identity groups agent history and locates project-owned swe-mux state.
- `.swe-mux/config.toml` contains versioned project-only defaults.
- `.swe-mux/notes/` contains human-readable Markdown tied to stable space/session IDs.

## Root and precedence

- A Git worktree uses its own worktree root, never the common Git directory or a parent
  checkout. A non-repository session falls back to normalized cwd.
- Spawn precedence is request override → space override → project config → global config
  → daemon cwd/default.
- Project config permits only friendly label, relative default cwd, default shell profile,
  and notes enabled. Auth, bind, ports, secrets, hooks, raw executable, and commands are
  rejected. The default cwd cannot be absolute or contain a parent traversal.
- Merely resolving/reading a project creates nothing. The first explicit Settings or note
  save creates the `.swe-mux/` path through atomic replacement.

## Notes

- Space notes live at `.swe-mux/notes/spaces/<safe-stable-id>.md`; session/history notes
  use `sessions/`. Unsafe IDs map deterministically to a digest filename while front matter
  preserves the original identity.
- Rename and move do not rename a note because IDs remain stable. Deleting a space/history
  entry leaves its Markdown as an intentional orphan for Git/manual recovery; swe-mux does
  not silently delete user-authored files.
- Reads expose a content revision. Saves require that revision, so external editor or Git
  changes produce a visible conflict instead of being overwritten.
- Editing, preview, search, export, terminal-selection capture, and insertion are explicit
  UI actions. A note is never injected into agent context automatically.
- The quick editor is a centered, Settings-style modal. A live session or space note can
  also be docked as a persistent recursive-layout leaf beside its terminal. Docking defaults
  to a narrower right column, uses the existing draggable divider, and never creates a
  second note file. Closing or popping out the pane changes only layout membership; pending
  edits are saved first and the Markdown remains in the project.
- Docked note identity is `spaces:<encoded-space-id>` or `sessions:<encoded-session-id>`.
  This keeps layout membership stable across note saves and prevents the same note from
  appearing twice in one layout. Quick-modal and pane instances are mutually exclusive for
  one docked note, avoiding conflicting autosave writers.
- Space notes open from space right-click, empty-workspace right-click, `: menu`, and the
  palette. Session notes open from session/terminal right-click and the pane-header `note`
  action. The same menus and palette expose explicit `note in split` actions, and the quick
  editor exposes `dock right`. Ended-session annotations remain editable in the modal,
  while docking, terminal capture, and insertion are disabled when no live terminal owns
  that note identity.
- Terminal-selection capture carries an editor target key through the terminal event path,
  so two visible note panes cannot both consume one capture action.
- Malformed, missing, read-only, disabled, and conflict states remain local to the surface;
  terminals continue running.

## Key files

- Identity: `src/swe_mux/projects.py`
- Storage and validation: `src/swe_mux/project_files.py`
- Notes UI and pane routing: `frontend/src/Notes.tsx`, `frontend/src/App.tsx`,
  `frontend/src/layout.ts`
- Project Settings: `frontend/src/Settings.tsx`
