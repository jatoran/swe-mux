# Frontend: Project resources and the note editor

Index: `../packages.md`.
Design: `../../../design/features/project-resources.md`.

## Project resources

`ProjectResource.tsx`, `ProjectNoteEditor.tsx`, `DelimitedTextViewer.tsx`, `ImageViewer.tsx`,
`delimitedText.ts`, `projectResourceCreate.ts`, `noteSaveQueue.ts`, `noteEditGuard.ts`,
`noteEditorSettings.ts`, `noteFind.ts`,
`noteOutline.ts`, `noteScroll.ts`, `layoutBox.ts`, `fileClipboard.ts`

- Project notes plus the `global-note:scratchpad` editor, and canonical and exact-worktree file editors.
- Right-click and guarded-long-press exclusive canonical creation, with pure destination selection.
- Bounded CSV/TSV parsing with a virtualized table preview, and revision-pinned allowlisted image display.
- Root-aware save and watch isolation.
- Continuity rail clipboard actions with versioned leading-order migration.
- The pure config to editor-configuration resolution: element props versus `--continuity-*` properties, with chord-overlay sanitizing.
- Shared path, reveal, and clipboard actions across the Files tree and opened-file tabs.

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

`noteScroll.ts` is the measured jump: a DOM-free convergence loop over a four-call `ViewportScroller` (visible line window, scroll offset, scroll-to, viewport height) that brings a source line to the top of the projection without any pixels-to-lines conversion, which the editor deliberately does not export.

### The vendored editor's two hosting hazards

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
