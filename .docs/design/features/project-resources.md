# Project resources

## What it is

Safe access to Project and session notes, a bounded Project file tree, revision-checked text
editing, ignore patterns, host file-manager reveal, and leased filesystem watches. All resource
views participate in the same pane/tab layout as terminals and previews.

## Notes

- Every Project has one canonical note at `.swe-mux/notes/project.md`. Creation seeds an absent
  file with a `# <Project name> notes` heading followed by two blank lines. An existing note is
  never rewritten, and renaming a Project never rewrites its heading.
- Every shell, Claude, or Codex terminal can lazily initialize a distinct note at
  `.swe-mux/notes/sessions/<safe-session-id>.md`. Unsafe/external identities use a stable hashed
  filename. Opening an existing note never overwrites it.
- Each terminal's pane bar carries a one-click `note` chip that starts, opens, or focuses that
  terminal's note. It reports three states: empty, written (the note holds text), and open (the
  note is the focused tab). `session.note` is the same action from the command palette and its
  default `Ctrl+Alt+N` binding.
- A session note opens *beside* its terminal, never stacked over it: an already-open copy is
  focused, otherwise an existing non-terminal pane hosts it, otherwise the anchor pane splits and
  the note takes the smaller share. Every entry point (pane chip, context menu, palette, sidebar
  row) uses this rule. The note context menu's explicit `Open in focused pane` is unaffected.
- The browser's per-session note signal is content, not file presence: a note created by a stray
  chip click stays readable and writable but earns no sidebar row until it holds text. Note
  authorization still keys on file existence, so an empty note is never locked out.
- A session-notes browser lists every session note that holds text, filterable by Project and
  searchable over owner, Project, and excerpt. It opens from a Project's sidebar context menu
  (scoped to that Project), the main menu, or the `notes.browse` command. Selecting a row opens
  that note through the ordinary companion placement rule.
- The listing is derived from the filesystem, not from history, so a note stays reachable after
  its terminal is dismissed, its history row is pruned, and the daemon restarts. Live sessions
  and history rows only supply display labels; a note whose owner left no record anywhere still
  lists under its own note identity. This is the only UI path to a plain shell terminal's note,
  which History never shows and the file browser hides with the rest of `.swe-mux`.
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
- One shared Continuity editor renders every editable Markdown surface (project note, session
  note, and Markdown files opened from the browser) on desktop and mobile, and all of them
  autosave through the same resource-scoped queue. Only the save target differs: notes PUT the
  note endpoint (`{markdown, revision}`); Markdown files PUT the file endpoint
  (`{path, text, revision}`). The queue's debounce, in-flight coalescing, 409 conflict banner,
  retry, and teardown beacon are identical for both.
- The editor is remounted whenever a different document loads so a new engine cannot leak text
  between documents. Ordinary edits do not remount it, so cursor and undo history survive. A
  host replacement is an echo of pushed text and is never committed back.
- A Markdown file's change event carries no storage revision, so the browser cannot tell its own
  echoed write from an external one. It therefore does not auto-reload an open Markdown file on a
  file-change event; a genuine out-of-band edit surfaces as a 409 conflict banner on the next
  save, with the same reload/overwrite choices as a note conflict.

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
- A debounced search box at the top of the Files pane filters recursively by file name, file
  content, or both (a scope toggle). A non-empty query replaces the lazy tree with a flat,
  path-ordered result list (content matches show the first matching line); clearing it restores
  the tree. Results and the tree share the same open, context-menu, and drag behavior.
- Clicking a file opens it as a tab in the next available pane, never inside the Files pane
  itself; only when Files is the sole pane does the file open there. A file row can also be
  dragged out of the tree or results and dropped onto any pane as a tab or a new edge split,
  reusing the ordinary workspace-tab drop targets.
- The Project-folder chooser (Add project) has an equivalent debounced name filter over the
  listed folders; contents/both do not apply to a folder chooser.
- UTF-8 files up to 2 MiB open in revision-checked editor tabs. Binary and larger files remain
  discoverable but do not enter the text-edit path. Markdown files (`.md`/`.markdown`/`.mdx`)
  open in the shared Continuity editor and autosave like notes; every other text file uses a
  plain textarea with an explicit Save button and baseline diff.
- Global `project_ignore_patterns` and Project-local `ignore_patterns` compose. They filter the
  browser and watcher only, never Git. Settings preserves line breaks while editing and trims
  blank entries only on explicit Save.
- A file/folder context menu can reveal it in the host file manager, copy either path form,
  copy a file's contents, add its basename to global ignores, or add its Project-relative path
  to Project ignores. Windows reveal selects a file and asks Explorer to foreground its window.
  The tree, the search results, and an opened file's own resource tab all offer the copy group,
  so a path never has to be transcribed by hand or re-found in the browser.
- Both path forms are offered because both are the right answer somewhere: the absolute path is
  what pastes into a shell, the Project-relative one is what agents and commit messages use. The
  absolute form is rebuilt against the Project root's own separator, so a Windows root yields a
  backslash path even though the API carries paths as posix.
- Copying contents is bounded at **5,000 lines or 200,000 characters, whichever bites first**,
  cutting at a line boundary and appending a notice naming exactly how much was left behind.
  The bound is not about clipboard capacity (megabytes are fine) but about the destination:
  these copies are usually pasted into an agent prompt, where every line is paid for. The
  notice is load-bearing for the same reason — without it an agent reads a truncated file as
  the whole file. Binary files and anything above the server's 2 MiB read limit are refused
  with a reason rather than copied empty.
- A refused clipboard write (non-secure context, mobile activation loss) parks the payload in a
  recovery panel in the Files view instead of dropping it, so a blocked copy costs one tap.
- `Tab` inserts a literal tab in the plain-textarea file editor. Continuity Markdown surfaces
  handle their own indentation and list behavior.

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
- `frontend/src/SessionNotesBrowser.tsx`
- `frontend/src/layout.ts` (`placeCompanionLeaf`)
- `frontend/src/editorText.ts`
- `frontend/src/noteSaveQueue.ts`

## Relates to

- `projects.md`: registry ownership and portable Project configuration.
- `workspace-layout.md`: placement, movement, and close behavior.
- `history.md`: reopening a terminal-owned note from agent history.
