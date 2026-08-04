# Project resources

## What it is

Safe access to Project and session notes, a bounded Project file tree, revision-checked text
editing, ignore patterns, host file-manager reveal, and leased filesystem watches. Editable
resources (notes, file editors) are pane tabs alongside terminals and previews. The two
*navigators* — the file tree and the notes index — are utility-drawer tabs: they open documents
into panes rather than holding one, so they cost a panel instead of a permanent tab.

## Notes

- Every Project has one canonical note at `.swe-mux/notes/project.md`. Creation seeds an absent
  file with a `# <Project name> notes` heading followed by two blank lines. An existing note is
  never rewritten, and renaming a Project never rewrites its heading.
- The Project note has **no sidebar chip**. It is opened from the Notes drawer tab's first
  pinned row (always present, even when the note is empty), the Project context menu's
  `Project note`, the `notes.open` / `project.note` commands, or the Projects registry's
  per-Project note action. The sidebar chip it replaced cost every Project row a permanent
  second line for a surface most Projects never opened; see `ui.md` § sidebar.
- Every shell, Claude, or Codex terminal can lazily initialize a distinct note at
  `.swe-mux/notes/sessions/<safe-session-id>.md`. Unsafe/external identities use a stable hashed
  filename. Opening an existing note never overwrites it.
- **Every note file opens with a TOML identity header, and the editor never sees it.** The header
  records `swe_mux_note = 1` plus the note's kind and id; `_note_body` strips it on read and
  `write_note` rebuilds it on save, so the pair the file path was derived from is the only pair
  that can ever be stored. Reading normalizes newlines first and saving writes LF, because the
  header is matched byte-exactly: `read_note` decodes raw bytes to hash the file, so unlike the
  `read_text` callers it gets no universal-newline translation, and a note rewritten with CRLF
  would render its header as body text. That failure used to compound — the leaked header was
  submitted back on save and went unrecognized, stacking a second one — so the strip now peels
  every header it finds and heals an already-corrupted file. A repository `.gitattributes` pins
  these files to `eol=lf` so a checkout under `core.autocrlf=true` cannot reintroduce it.
- Each terminal's pane bar carries a one-click `note` chip that starts, opens, or focuses that
  terminal's note. It reports three states: empty, written (the note holds text), and open (the
  note is the focused tab). `session.note` is the same action from the command palette and its
  default `Ctrl+Alt+N` binding.
- A session note opens as a tab in the pane you were last in — the focused view when it is still
  in the layout, then the owning terminal's pane, then whatever the layout has. Every entry point
  (pane chip, context menu, palette, sidebar row, Notes tab) uses this one rule, which is the same
  rule the Project note and file editors already used.
- It used to split a pane off so the note sat *beside* its terminal rather than over it. That
  traded workspace geometry for a guess: it rearranged panes every time a note was opened, and
  the split it chose was rarely the one wanted. Splitting is an explicit action — the tab menu's
  split rows, or a drag onto a pane edge — not a side effect of opening something.
- A note that is already open is activated **where it is**, never moved. Reopening it from a
  different pane must not tear it out of the pane the user put it in. The note context menu's
  explicit `Open in focused pane` is the deliberate way to move it.
- The browser's per-session note signal is content, not file presence: a note created by a stray
  chip click stays readable and writable but earns no sidebar row until it holds text. Note
  authorization still keys on file existence, so an empty note is never locked out.
- The **Notes** drawer tab is both the index and a second host for the editor. The Project note is
  pinned first and is always present even when empty (it is the one note every Project has). The
  focused terminal's note is pinned second when it holds text. Below them sits every other session
  note with content, searchable over owner, Project, and excerpt, scoped to this Project or to all
  of them. Selecting a row opens that note **in the drawer**; the row's `⇥` opens it as a pane tab
  through the ordinary placement rule above instead.
- **A note has exactly one live editor per browser, and that is a correctness requirement.**
  `noteSaveQueue` holds one entry per `(Project, resource)` at module scope, so two mounted
  editors share it: each submits its whole document, newest wins, and the loser's text is dropped
  with nothing for the daemon to flag, because the revision each holds is correct. The second
  editor's load then calls `reset`, discarding whatever the first had pending. Two devices are
  safe by contrast — separate queues and revisions, so the second write 409s into the conflict
  banner — which is why only same-browser duplication has to be structurally prevented.
- The drawer's claim is therefore exclusive: while it holds a note, that note's pane leaf keeps
  its place in the layout but renders an "open in the panel" placeholder, and every pane placement
  (sidebar row, pane `note` chip, tab menu open/split, drag, `notes.open`) releases the claim
  first so it cannot land on that placeholder. The claim is **device-local** and stored per
  Project (`mux.drawer.note.v1`), never in `project.layout`, which is shared: a phone must not be
  able to rearrange the desktop's panes. It also only applies while the drawer is open, so closing
  the panel hands the note back to its pane and reopening resumes.
- Moving between hosts unmounts one editor and mounts the other, which is lossless because the
  save queue outlives both. The unmount flushes, and the arriving editor adopts any text the
  daemon has not acknowledged yet (`noteSaveQueue.pendingText`, which covers both debounced typing
  and the snapshot a running PUT is carrying) rather than the copy its own GET returned — that GET
  can be answered from the pre-flush file. Cursor and undo history do not survive a move; they do
  survive drawer tab switches, because the Notes body is kept mounted and hidden rather than
  unmounted (see `ui.md` — the other half of that is `insertTarget`, which refuses a detached
  editor and would otherwise route a Clipboard paste into a terminal).
- The tab replaced the session-notes modal and is reached from a Project's sidebar context menu
  (scoped to that Project), the main menu, the `notes.browse`/`notes.browseProject` commands, and
  its own icon on the desktop rail.
- Scope follows how you arrived, the same rule the rest of the app's browsers use. Reaching the
  tab from the rail icon, the tab strip, or `drawer.notes` says nothing about scope, so it means
  *this Project* — the drawer sits beside that Project's workspace. Only the app menu's
  deliberately unscoped `notes.browse` widens it to every Project. Without that reset the flag
  would stick: one visit through the app menu would leave every later open showing all Projects.
  The reset lives in `showDrawerTab`, so every scope-less entry point inherits it, while the two
  scoped entry points go through `openNotesBrowser`, which is not on that path.
- The scoped listing follows the active Project rather than pinning the one it opened with, so
  switching Project with the tab open refetches instead of showing a stale Project's notes.
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
- The vendored Continuity 0.2.18 editor owns mobile touch arbitration. `pointerdown` does not
  focus its textarea; a resolved tap projects the caret and focuses synchronously, while scroll,
  cancellation, and long-press paths leave the keyboard closed. swe-mux does not inspect the
  editor's shadow DOM or duplicate caret hit-testing for this behavior.
- The editor is remounted whenever a different document loads so a new engine cannot leak text
  between documents. Ordinary edits do not remount it, so cursor and undo history survive. A
  host replacement is an echo of pushed text and is never committed back.
- A tab switch *does* remount it — only the active child of a stack renders — which used to
  end undo history at the pane boundary: you could undo while editing a note, but not after
  leaving it and coming back. The history is now exported on the way out and re-imported on the
  way in, beside the scroll state that was already carried across the same unmount. It is
  bounded on both axes (newest groups per note, most-recently-left notes overall) because a
  history is much larger than a viewport offset. An import into text that changed underneath is
  refused by the editor and dropped here, since that history describes bytes the note no longer
  has — an agent rewrite or a conflict resolved from disk starts a fresh history rather than
  replaying edits against the wrong content.
- A Markdown file's change event carries no storage revision, so the browser cannot tell its own
  echoed write from an external one. It therefore does not auto-reload an open Markdown file on a
  file-change event; a genuine out-of-band edit surfaces as a 409 conflict banner on the next
  save, with the same reload/overwrite choices as a note conflict.
- A resource load or refresh that fails is transient state, never a dead tab: reads carry a
  request timeout (so a request that hangs while a dormant client wakes fails instead of waiting
  forever), failures retry on a backoff, and any resume signal retries at once. The banner offers
  an immediate retry while one is queued. A retry on a note that already holds content goes
  through the same clean-state live-follow check, so recovering a failed refresh can never
  discard unsaved local typing. Autosave PUTs carry the same timeout, so a save that hangs cannot
  leave the queue permanently in-flight (which would block every later save and every
  live-follow); it fails into the existing retry, which a resume also short-circuits.

### Editor preferences

- Settings → Notes configures the one shared editor, so every Markdown surface moves together:
  spellcheck, Markdown projection on/off (`plain` keeps undo, multi-cursor, list continuation,
  and autosave and only stops the rendering), what `Tab` does, typography, the touch command
  rail, and the editor's own keyboard shortcuts.
- The knobs are exactly what the vendored editor exposes: element properties/attributes
  (`spellcheck`, `syntax`, `tab-behavior`, `shortcut-policy`, `command-rail`, `indent-guides`)
  and its `--continuity-*` custom properties. Nothing is emulated on top, so a setting either
  maps to the editor or is not offered.
- Indent guides are the one place we deliberately differ from the editor's own default. It
  ships them off so that upgrading it changes no embedder's appearance; notes here are mostly
  nested lists, so they are on unless the config says otherwise — and *only* an explicit
  `false` turns them off, so a daemon too old to know the key does not silently land on the
  editor's default instead of ours.
- Colours are deliberately not among them. The theme already maps the app palette onto the
  editor's colour variables, and a second source for them would fight the theme.
- A blank/zero typography value means *keep the editor's default* rather than pinning today's
  value here, so upgrading the vendored editor can still move its defaults.
- Preferences reach a live editor as element configuration, never as a remount: remounting
  would drop undo history and reseed from the last *loaded* text, discarding edits since. The
  editor pins measured typography from a ResizeObserver on its own box, which a font change
  does not move, so applying typography also nudges live editors into re-measuring rather than
  leaving caret geometry stale until the next reflow.
- Shortcuts are stored as an *overlay* on the editor's own table (chord → command, or released
  to the browser), never as a copy of the table, so a chord left alone still follows the editor
  across an upgrade. TOML has no null, so a released chord is stored as `""` and the browser
  maps it back to the `null` the editor wants. Two chords the editor binds but flags
  non-browser-safe (bullet and task toggles) are reclaimed by the shipped default.
- The rail's *arrangement* — which buttons, in what order — stays owned by the editor, which
  persists it per device from its own gear panel. Settings only offers a reset, which therefore
  applies immediately and is not part of the Save/Cancel draft.

### Finding text in a note

- Every Continuity-backed view carries a find bar with the same shape as the terminal's:
  query, previous/next, match case, an `n/total` readout, and Escape to leave.
- Locating matches is the app's job, not the editor's. The editor has no find, and the
  browser's cannot stand in for one: the projection realizes only the lines in the viewport, so
  an offscreen match is not in the DOM to be found. Matching runs over the engine snapshot and
  the resulting ranges are handed back for painting through the editor's host decoration API
  (two sets: every match, and the current one in a stronger tint).
- Matching is plain substring, never a regular expression: the query is typed over prose, so an
  unescaped `(` would either throw or quietly match something else. A query spanning a line
  break never matches, because editor ranges are line-scoped.
- Decorations are positions rather than anchors, so an edit underneath a live set would leave
  it marking the wrong bytes. The set is therefore recomputed on every commit while the bar is
  open, not only when the query changes.
- Four ways in, matching the places a user is: the visible resource-header button shared by
  workspace-tab and drawer notes, `Ctrl+F` while the editor has focus, a `mux:find` button on the
  touch rail, and a `note.find` command for the palette, a gesture, or a chord of the user's own.
  `Ctrl+F` is deliberately *not* a global app binding — the ask is
  for the focused note, and a global chord would have to swallow the browser's own find
  everywhere else to get it. The command instead dispatches a cancelable event that the
  resource holding the focused editor claims; an unclaimed event is how "no note is focused"
  is detected, since which editor has focus is not app state.
- Leaving the bar selects the match the user stopped on, so the next edit happens where they
  were looking rather than where the caret sat before the search.

### Sending a selection to an agent

- Every Continuity-backed view (project note, session note, Markdown file) offers **send to
  agent**: the highlighted text, or the whole document when nothing is highlighted, becomes the
  first message of a new Claude/Codex session or is delivered to a live one through the prompt
  queue. Plain-text file editors offer it too (always the whole document — they own no
  selection engine), and the Files tree context menu offers "Send to an agent session" with
  the same fetch/size/binary gating as its copy action.
- The action has two hosts. A `→ agent` button in the pane header serves desktop (Continuity's
  own rail is touch-only), and the same action is registered on that rail as `mux:send-to-agent`
  for touch, where a tap keeps the keyboard and the selection in place. Both read the selection
  from the engine snapshot, not from DOM focus, so taking focus to click cannot lose it.
- The dialog captures the message when it opens, so editing the document underneath cannot
  change what is about to be sent. The message is shown in full and stays editable: this is a
  user-initiated send, and the text on screen is the text that leaves. A selection enters the
  message box without an origin preamble; the source remains visible in the dialog heading but
  is not sent. Whole-document sends retain one origin line naming the document.
- A project/session-note selection offers **Remove the selected text from the note**, on by
  default. Removal happens only after the handoff is accepted: immediate delivery, durable queue
  staging (including **Add to queue**), composer fill, or successful new-session seeding. Cancel,
  error, and blocked/refused delivery leave the note untouched. Whole-document and file sends do
  not offer the toggle. A truncated selection cannot offer it because deleting text that was not
  included in the payload would lose data. A mounted editor applies the captured Continuity
  selection as one undoable edit; a mobile drawer editor that has already detached commits the
  same engine-produced snapshot through the resource save queue that survives its unmount.
- Targets are the live Claude/Codex sessions of a chosen Project (any Project, defaulting to the
  document's own), plus "new Claude session" and "new Codex session". Shell sessions are never
  offered: an agent composer holds a paste inert until submitted, while a shell would run it.
  Ended, crashed, and still-spawning sessions are excluded, and switching Project drops a target
  that does not exist there rather than silently retargeting.
- A new session is seeded through `seed_text` on the spawn request and opens beside the pane
  the text came from — that avoids writing into a TUI that is not ready for input yet. The
  daemon inlines a short body into the agent CLI's argv (a leading `-` offset by one space so
  the CLI cannot read it as a flag) and stages a body over the argv bound into
  `.swe-mux/seeds/` with a short reader prompt, so there is no client-side length ceiling.
- Sending to a live session is a **prompt queue** operation (`features/prompt-queue.md`): the
  dialog stages the message armed and asks the queue to deliver it now, and also offers "Add
  to queue" to stage without delivering. An occupied queue holds the message in strict order
  instead of racing the composer; a not-safe target is refused by the daemon with typed
  reasons and the button becomes the explicit "Send anyway" confirmation (never available for
  approval/Q&A or a replaced target). With "Press Enter after inserting" off, the text only
  fills the composer over `POST /api/sessions/{id}/input` (bracketed paste, no submit) — a
  fill, not a delivery, so it does not enter the queue.

### Known nested-Project gap

Note read/write/initialize currently re-resolves the registered root through the Git-aware
`resolve_project` helper. If a configured Project is a subfolder of a larger Git worktree, that
helper can select the enclosing worktree's `.swe-mux/notes/` instead of the explicit Project's
directory. This is an implementation defect, not an ownership exception: note operations must
ultimately use the already validated Project identity/root directly. Regression coverage should
include a registered Project nested below another Git root.

## Files and ignores

- The file browser is the utility drawer's **Files** tab, scoped to the active Project. It is
  lazily expanded from the canonical Project root. Traversal and symlink escapes are rejected;
  one directory response is capped at 2,000 entries. Its header carries the Project root, which
  is the only thing naming which tree this is once it stopped being a labelled pane tab.
- A debounced search box at the top of the Files tab filters recursively by file name, file
  content, or both (a scope toggle). A non-empty query replaces the lazy tree with a flat,
  path-ordered result list (content matches show the first matching line); clearing it restores
  the tree. Results and the tree share the same open, context-menu, and drag behavior.
- Clicking a file opens it as a tab in the focused pane and, on mobile, closes the drawer that
  was covering it. A file row can also be dragged out of the tree or results and dropped onto any
  pane as a tab or a new edge split, reusing the ordinary workspace-tab drop targets — desktop
  only, because the drawer is an in-flow column there and an overlay on mobile, where there is no
  visible pane to drop onto.
- The Project-folder chooser (Add project) has an equivalent debounced name filter over the
  listed folders; contents/both do not apply to a folder chooser.
- UTF-8 files up to 2 MiB open in revision-checked editor tabs. Markdown files
  (`.md`/`.markdown`/`.mdx`) open in the shared Continuity editor and autosave like notes;
  every other text file uses a plain textarea with an explicit Save button and baseline diff.
  CSV and TSV use the same file tab but default to a bounded, virtualized table preview with a
  raw-text toggle; raw mode remains the only editable representation.
- PNG, JPEG, GIF, and WebP files open read-only in the same ordinary file tabs. The server reads
  at most 16 MiB, verifies that the decoded format matches the allowlisted extension, and refuses
  images above 16,384 pixels on either axis, 25 million pixels per frame, 200 frames, or 100
  million total decoded pixels. The revision-pinned content route serves only images that pass
  those checks, with an exact allowlisted MIME type, `nosniff`, `no-store`, and a sandbox CSP.
  SVG, PDF, archives, video, audio, and other browser-active or unsupported formats remain
  discoverable and open in a tab that explains why no preview is available.
- File inspection checks the regular-file type and declared size before a bounded read, then runs
  blocking filesystem and image work off the server event loop. Refused oversized files do not
  require hashing the full body to produce a revision.
- Global `project_ignore_patterns` and Project-local `ignore_patterns` compose. They filter the
  browser and watcher only, never Git. Settings preserves line breaks while editing and trims
  blank entries only on explicit Save.
- A file/folder context menu can reveal it in the host file manager, copy either path form,
  copy a file's contents, add its basename to global ignores, or add its Project-relative path
  to Project ignores. Windows reveal selects a file and asks Explorer to foreground its window.
  The tree, the search results, and an opened file's own resource tab all offer the copy group,
  so a path never has to be transcribed by hand or re-found in the browser.
- Right-click or guarded touch long-press opens the Files actions; there is no permanent create
  button. **New file** and **New folder** target the clicked directory, the containing directory
  of a clicked file, or the Project root when invoked on empty tree/results space. A naming dialog
  shows that destination. Creation accepts one leaf only, rejects Windows-invalid/reserved names
  and `.git`/`.swe-mux`, requires an existing contained parent, and uses exclusive filesystem
  creation: an existing file, directory, or symlink returns `409 resource_exists` and is never
  changed. A new file opens in its normal pane tab; a new folder clears search, refreshes its
  parent, and expands. Items hidden by ignore rules are still created, with a persistent notice.
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

## Terminal attachments

- An open Claude or Codex terminal accepts files from OS drag/drop, a copied-file paste, or the
  action rail's **Attach** picker. One browser action may carry up to 10 files. Shell sessions and
  ended agent sessions do not expose the action.
- The daemon copies each file into
  `<workspace>/.swe-mux/attachments/<safe-session-id>/<uuid>-<safe-name>` and creates a local
  `attachments/.gitignore` that ignores the directory contents. `<workspace>` is the registered
  Project root when the session cwd is inside it; a session deliberately launched at an external,
  Git-validated worktree root uses that worktree instead. Runtime and later agent-run cwd
  telemetry are never used for storage selection.
- General files are opaque transport. CSV, TSV, Excel workbooks, PDFs, and source files are all
  accepted, but swe-mux does not parse or convert them. The terminal draft receives the absolute
  path and the agent decides which reader or tool to use. The insertion never presses Enter.
- PNG, JPEG, WebP, and GIF are content-sniffed and keep the native Claude/Codex image-paste path.
  An empty browser MIME type is accepted when the bytes identify the format; misleading image
  MIME/extension data is refused. Codex attachments are held until its bracketed-paste mode says
  the chat prompt is ready, so a file cannot be uploaded and then lost during CLI startup.
- Attachment insertion is always unicast, even when the source pane has terminal broadcast on.
  Files are capped at 25 MiB each; native images retain the tighter 10 MiB cap. A session may own
  at most 32 attachments and 100 MiB total. Files persist across daemon restarts and session
  removal until the user removes them from the workspace; there is no silent TTL cleanup.

## Watch efficiency

- Only open tree directories and parents of open file tabs renew short watcher leases.
- Watches are non-recursive and changes are coalesced. Expanded descendants obtain their own
  leases; closed, collapsed, expired, and ignored directories consume no watcher.
- Watch events invalidate visible resource state. They do not bypass revision checks or turn
  filesystem changes into Git behavior.

## View lifetime

Project note, session notes, and file editors are ordinary `note`-kind layout leaves with typed
resource IDs. They can share a pane, move between panes, or create a pane-edge split. Closing a
resource tab closes only the viewport: it never deletes the underlying file. Moving a file editor
preserves its unsaved draft.

The file browser is not a leaf. Its expanded-folder set was never layout state anyway — it
persists per Project in the shared `fileTree` settings domain, so it survives the move to the
drawer, a reload, and a different device. A `files:` leaf persisted by an older client is pruned
on read (`workspace-layout.md`).

## Key files

- `src/swe_mux/project_files.py`
- `src/swe_mux/project_watcher.py`
- `src/swe_mux/file_manager.py`
- `frontend/src/ProjectResource.tsx`
- `frontend/src/DelimitedTextViewer.tsx`
- `frontend/src/ImageViewer.tsx`
- `frontend/src/delimitedText.ts`
- `frontend/src/projectResourceCreate.ts`
- `frontend/src/ProjectNoteEditor.tsx`
- `frontend/src/NotesTab.tsx` (the notes index; hosted by `UtilityDrawer.tsx`, which also hosts
  the drawer's note editor)
- `frontend/src/drawerNotes.ts` (which note the drawer holds, and why one host at a time)
- `frontend/src/layout.ts` (`openAnchorId`, `openTab`)
- `frontend/src/editorText.ts`
- `frontend/src/noteSaveQueue.ts`
- `frontend/src/noteSelection.ts` (selection slicing, message composition, delivery payloads)
- `frontend/src/agentTargets.ts` (which sessions may receive a send)
- `frontend/src/SendToAgentPicker.tsx`

## Relates to

- `projects.md`: registry ownership and portable Project configuration.
- `workspace-layout.md`: placement, movement, and close behavior.
- `history.md`: reopening a terminal-owned note from agent history.
