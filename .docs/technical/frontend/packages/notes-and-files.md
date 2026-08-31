# Frontend: Project resources and the note editor

Index: `../packages.md`.
Design: `../../../design/features/project-resources.md`.

## Project resources

`ProjectResource.tsx`, `ProjectNoteEditor.tsx`, `LazyCodeEditor.tsx`, `CodeEditor.tsx`,
`codeLanguage.ts`, `codeTheme.ts`, `DelimitedTextViewer.tsx`, `ImageViewer.tsx`,
`delimitedText.ts`, `projectResourceCreate.ts`, `noteSaveQueue.ts`, `noteEditGuard.ts`,
`noteEditorSettings.ts`, `noteFind.ts`,
`noteOutline.ts`, `noteScroll.ts`, `layoutBox.ts`, `fileClipboard.ts`, `recentFiles.ts`,
`fileSearchLimit.ts`

- Project notes plus the `global-note:scratchpad` editor, and canonical and exact-worktree file editors.
- `NotesTab.tsx` owns the Scratchpad and empty-rail context menus; `App.tsx` mirrors the hot `note_scratchpad_enabled` config, persists direct toggles, and removes disabled Scratchpad leaves from Project layouts without deleting note storage.
- Right-click and guarded-long-press exclusive canonical creation, with pure destination selection.
- Bounded CSV/TSV parsing with a virtualized table preview, and revision-pinned allowlisted image display.
- Root-aware save and watch isolation.
- Continuity rail clipboard actions with versioned leading-order migration.
- The pure config to editor-configuration resolution: element props versus `--continuity-*` properties, with chord-overlay sanitizing.
- Shared path, reveal, and clipboard actions across the Files tree and opened-file tabs.
- The Files tab's three mutually exclusive bodies - the lazy tree, the flat search results, and the Recent list - with search winning over Recent because typing a query is an explicit act.

### Search results belong to one query

`ProjectResource.tsx` clears the prior result rows as soon as the trimmed query or search mode changes and renders the pending query in a live status line.
The effect aborts its previous HTTP request and also guards response application by generation, because cancellation and response completion can race.
Keeping old rows visible under new input is forbidden: a broad one-letter query can return immediately while a narrow query walks to the file bound, making the unchanged rows look like the new query did nothing.

### The source editor is two dynamic boundaries, not one

`LazyCodeEditor.tsx` is the boundary for CodeMirror's core (view, state, commands, language, autocomplete, search, and this app's theme layer), on the same pattern as `LazyGitDiff.tsx`.
`codeLanguage.ts` is the second: every one of its ~28 grammars is behind its own `import()`, so a session that opens one `.ts` file fetches one grammar rather than the set.
Both were static, and together they were the largest avoidable part of the entry chunk - paid for on every page load, including phones that never open a file.

Two consequences worth keeping:

- `languageLoaderForFilename` is **synchronous and returns a loader**, not a promise of an extension.
  Naming a file must not fetch anything, and "plain text" has to be distinguishable from "a grammar that has not arrived yet" without awaiting.
  `CodeEditor` therefore creates the view with an empty language compartment and reconfigures the grammar in when it lands: the document is readable immediately, and the alternative - awaiting the grammar before mounting - would put a spinner in front of every file to buy colours a frame earlier.
- Every dynamic specifier is also listed in `vite.config.ts`'s `optimizeDeps.include`.
  Dev answers a dependency it first discovers at runtime with a full page reload, which in the renderer suite lands mid-spec; `bundleSplit.test.ts` fails if the two lists drift apart.

`CodeEditor` reconciles an external `value` against its document, and does it by counting its own echoes.
The parent stores each string `onChange` hands it and re-renders, so it is a turn behind the keyboard: during a burst the effect runs with a document the editor has already moved past.
Comparing strings cannot tell that from a genuine external rewrite - both are simply "not the current document" - and treating it as one replaces the document with an older copy of itself, which re-emits, which replaces it again.
`lastEmitted` answers the caught-up case by reference (no second serialization per keystroke), and `pendingEchoes` answers the lagging one; only a value arriving with no echoes outstanding is an external change.

### Recent is Git's answer, phrased

`recentFiles.ts` is pure and clock-injected, and turns one Recent row into the line beside its name.
The two row kinds answer "when" in two different currencies, which is the whole reason the module exists rather than one format string: an uncommitted change has no timestamp Git records (and the file's mtime is exactly the filesystem reading the view exists to avoid), so it states *what* changed, while a committed path has a committer date and states how long ago.
The age is coarse on purpose - it is a sort key made readable, and a precise one invites reading it as authoritative when it is the committer's clock rather than this machine's - and it clamps at zero, because a committer clock ahead of this browser's must not render as a future age.
The list itself is read whole from `GET /api/projects/{id}/files/recent` (see `recent_files.py`); nothing is derived from the filesystem here.

### A truncated search says which bound bit

`fileSearchLimit.ts` is pure, and turns the search payload's `truncated_reason`/`stopped_at` into
the one line under the result list.
It exists because the two reasons deserve **opposite** advice and the surface shipped giving one
of them the other's.
Hitting the *result* limit means there is more of what you asked for, and refining finds it.
Hitting the *file* limit means the walk gave up before visiting the tree, so the matches on screen
are not the best matches but whatever was reached - and a narrower query re-runs the same walk and
gives up in the same place, which sends a reader retyping while nothing changes.
The file-limit notice therefore names the folder the walk stopped in and points at the ignore
list, and never says "refine".
A payload carrying `truncated` with no reason - an older daemon, which the redeploy rollback path
makes real - falls back to the result-limit sentence rather than going silent, because a notice is
all the reader gets there.

### What makes a commit a save

`noteEditGuard.ts` is pure and clock-injected, and `noteSaveQueue` holds one guard per entry, so
the policy survives an editor unmount the way the storage revision does.
`reset` takes the loaded document because that is the baseline the guards judge against; it is a
required argument rather than an optional one, since a caller that forgot it would silently lose
the protection.
`canonicalNoteText` erases only what markdown cannot render, and a hard line break (two or more
trailing spaces) survives it deliberately - collapsing that to nothing would drop a break the
user typed.

The input signal is the part to be careful with, because guard 3 keys on its absence.
It is only ever a *trusted* event on the editor element (`watchLocalInput`), so nothing this app
or the engine dispatches can forge one, and `pointerdown` counts because Continuity's own command
rail edits through taps that emit no key event.
Text arriving from elsewhere in the app is announced by the wrapped insert handle
(`insertHandle`), not by an event: the clipboard picker, a terminal selection, and voice Append
all reach the editor through `insertTarget`, whose trusted event happened on some other element.
A refused commit never touches text the entry already has pending - that text is earlier local
work still owed to the daemon, and dropping it would turn a loop guard into data loss.

### Pure text analysis

`noteFind.ts` and `noteOutline.ts` run over the engine snapshot with no runtime import from the editor package.
`noteFind.ts` produces substring match ranges.
`noteOutline.ts` produces the ATX heading list with its fenced-code exclusion, position-to-heading lookup, and distinct-level depth ladder.
The lookup is fed the first visible line, since a jump moves the viewport and never the caret.
`mobileGestures.ts` treats the Notes document rail, Project-note header, and Continuity command-rail part as one downward-pull outline region.
`App.tsx` resolves the editor within the touched Project-note surface and names it in the existing `mux:note-outline` claim event.

`noteScroll.ts` is the measured jump: a DOM-free convergence loop over a four-call `ViewportScroller` (visible line window, scroll offset, scroll-to, viewport height) that brings a source line to the top of the projection without any pixels-to-lines conversion, which the editor deliberately does not export.

### The editor's two hosting hazards

Note teardown calls the editor's `commitComposition()` before flushing the save queue, because a composing run is withheld from the engine and so has never been emitted as a change.
The SDK's own commit inside `destroy()` is too late for this host, whose adapter unbinds its listeners at ref detach.

`layoutBox.ts` is the DOM-narrow mount gate the editor element is created behind: `getClientRects()` for "does this slot generate a box at all", and a `ResizeObserver` for the reveal - never an `IntersectionObserver`, because the transition is *gained a box*, not *scrolled into view*.
The report is deferred one frame so mounting inside the callback cannot raise `ResizeObserver loop completed with undelivered notifications`, and a no-observer host reports immediately rather than never.
It exists because Continuity's first render measures inline-code affordances against `offsetParent`, which is null under `display:none`, and because hidden-but-mounted is the drawer's normal state.

`frontend/vite.config.ts` excludes `@continuity-editor/editor` from dev pre-bundling for a neighbouring reason: pre-bundling rewrites the module URL its WASM loader resolves against, so `npm run dev` serves the SPA fallback in place of the `.wasm` and every note editor fails to start there - which also makes the engine untestable from the renderer suite.

## Send a selection to an agent

`noteSelection.ts`, `agentTargets.ts`, `SendToAgentPicker.tsx`

Pure Continuity-snapshot slicing (UTF-8 byte offsets to UTF-16), message composition, bracketed-paste payload rules, and which sessions may receive a send.
The dialog stages and asks the queue to deliver, and owns the explicit "send anyway" retry.
`App.tsx` owns spawn, composer-fill, and placement.
