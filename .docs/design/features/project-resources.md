# Project resources

## What it is

Safe access to Project and session notes, a bounded Project file tree, revision-checked text
editing, ignore patterns, host file-manager reveal, and leased filesystem watches. All resource
views participate in the same pane/tab layout as terminals and previews.

## Notes

- Every Project has one canonical note at `.swe-mux/notes/project.md`.
- Every shell, Claude, or Codex terminal can lazily initialize a distinct note at
  `.swe-mux/notes/sessions/<safe-session-id>.md`. Unsafe/external identities use a stable hashed
  filename. Opening an existing note never overwrites it.
- A terminal and its nested agent runs share the terminal's stable `note_id`. Agent History rows
  retain that identity so `Session note` can reopen the same file after exit or daemon restart.
- Session-note initialization accepts a live terminal, a History row owned by the Project, or a
  note file already owned by that Project. Arbitrary client-supplied note identities are rejected.
- Project and session notes autosave through separate revision-safe queues. Note identity is
  part of the save key, so editing one note cannot overwrite another.
- Successful saves emit the note identity and storage revision. Other connected browsers
  live-follow by refetching and replacing an open note only while their resource queue is clean;
  pending, in-flight, failed, or conflicted local work is never replaced. Reconnect performs the
  same clean-state revision check so a browser returning from suspension catches up.
- A browser ignores its own echoed save event by comparing storage revisions. Simultaneous edits
  remain intentionally non-merged and use the existing optimistic revision conflict flow.
- The controlled Continuity editor creates and reconciles its document in layout effects.
  Passive prop synchronization can replay a stale pre-keystroke value and produce an apparent
  render/input loop.
- Narrow or coarse-pointer clients use a browser-native textarea backed by the same save queue.
  Mobile IME/autocorrect input therefore has one browser-owned mutation path; it never passes
  through the desktop projection engine's canceled `beforeinput` reconciliation.

### Known nested-Project gap

Note read/write/initialize currently re-resolves the registered root through the Git-aware
`resolve_project` helper. If a configured Project is a subfolder of a larger Git worktree, that
helper can select the enclosing worktree's `.swe-mux/notes/` instead of the explicit Project's
directory. This is an implementation defect, not an ownership exception: note operations must
ultimately use the already validated Project identity/root directly. Regression coverage should
include a registered Project nested below another Git root.

## Files and ignores

- The file browser is lazily expanded from the canonical Project root. Traversal and symlink
  escapes are rejected; one directory response is capped at 2,000 entries.
- UTF-8 files up to 2 MiB open in revision-checked editor tabs. Binary and larger files remain
  discoverable but do not enter the text-edit path.
- Global `project_ignore_patterns` and Project-local `ignore_patterns` compose. They filter the
  browser and watcher only, never Git. Settings preserves line breaks while editing and trims
  blank entries only on explicit Save.
- A file/folder context menu can reveal it in the host file manager, add its basename to global
  ignores, or add its Project-relative path to Project ignores. Windows reveal selects a file
  and asks Explorer to foreground its window.
- `Tab` inserts a literal tab in note/file editors. Note `Enter` carries leading indentation,
  and soft-wrapped lines retain that visual indentation.

## Watch efficiency

- Only open tree directories and parents of open file tabs renew short watcher leases.
- Watches are non-recursive and changes are coalesced. Expanded descendants obtain their own
  leases; closed, collapsed, expired, and ignored directories consume no watcher.
- Watch events invalidate visible resource state. They do not bypass revision checks or turn
  filesystem changes into Git behavior.

## View lifetime

Project note, session notes, Files, and file editors are ordinary `note`-kind layout leaves with
typed resource IDs. They can share a pane, move between panes, or create a pane-edge split.
Closing a resource tab closes only the viewport: it never deletes the underlying file. Moving a
file editor or Files tab preserves its unsaved draft or expanded-tree state.

## Key files

- `src/swe_mux/project_files.py`
- `src/swe_mux/project_watcher.py`
- `src/swe_mux/file_manager.py`
- `frontend/src/ProjectResource.tsx`
- `frontend/src/ProjectNoteEditor.tsx`
- `frontend/src/editorText.ts`
- `frontend/src/noteSaveQueue.ts`

## Relates to

- `projects.md`: registry ownership and portable Project configuration.
- `workspace-layout.md`: placement, movement, and close behavior.
- `history.md`: reopening a terminal-owned note from agent history.
