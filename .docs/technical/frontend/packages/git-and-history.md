# Frontend: Git, landing, History, and transcripts

Index: `../packages.md`.
Design: `../../../design/features/git.md`, `../../../design/features/land-queue.md`, `../../../design/features/history.md`, `../../../design/features/transcript-branches.md`.

## Git tab

`GitTab.tsx`, `gitWorktrees.ts`, `gitReview.ts`, `GitFileRow.tsx`, `GitReviewModal.tsx`,
`GitSessionLinks.tsx`, `LazyGitDiff.tsx`, `GitDiffView.tsx`

Map, Log, and durable session-provenance orchestration.
Mutations are limited to API-wrapped worktree add and remove, land *requests*, the one-key `[worktree] verify_command` write, and the gate approval.
Editing never approves, and nothing in the tab moves a trunk.

### Session links

`GitSessionLinks.tsx` is the pointer-anchored list every session-naming badge opens: a worktree's live occupants, a commit's session links, a provenance row.
It also owns `sessionLinkDestination`, the one rule deciding pane versus History versus inert, which `GitTab.tsx` applies to the ledger's own name links too so the popover and the rows cannot disagree.
The count badges are siblings of their row's expand button rather than spans inside it, because a button cannot contain a button.
The list registers a dismiss level so back closes it and not the drawer.

### Provenance presentation

Commit-to-session badges; committer, integrator, contributor, and branch-author role labels; contributed-file lists; confidence; and named ambiguity presentation.
Commit-grouped ledger cards name the committer - or, on a merge, the integrator and the branch's authors - and the contributors individually, and collapse everyone else into one occupancy line.
That split is taken from the daemon's own rollup, so the rule for "who made this" has one home, and an unknown role renders as an observer rather than as an authorship claim an older build would have to invent.
A separate reference-movements section renders checkout facts in plain language and never in the shape of a session claim.
Provenance is carried into commit review packets.

### Map and Log

Width-safe Map rows with one deduplicated identity line and a separate wrapping metric line.
Activity ordering is `sortWorktreesByActivity`, sorted once in `GitTab.tsx` and the tab's only list of checkouts.
It is keyed on the branch tip's committer date rather than the checkout's `st_mtime`, which Windows freezes on a worktree a live session is holding open.
The main tree is pinned first as the anchor the rest are measured against, undated trees last, with path order as the tie-break so a refresh cannot shuffle the list under the pointer.
Parsing stays faithful to the payload; ordering is a presentation decision on top of it.

Violet emphasis marks nonzero comparison-ahead counts.
Log draws TUI graph context, colored Git-authored lanes and nodes, semantic ref ordering, and exact commit-OID worktree-tip markers, without hiding any refs.

Explicit unavailable and prunable presentation never converts null measurements to clean.
Also: read-specific timeout guidance, failed-removal refresh with the mutation error retained, review locators, ephemeral annotation anchors, stale-session reduction, bounded review-packet generation, shared file rows, neutral comparison labels, and the adaptive review modal.

`LazyGitDiff.tsx` is the dynamic boundary for `react-diff-view`, so the parser and stylesheet load only after a patch is requested.

## Landing

`GitLandRow.tsx`, `GitLandBar.tsx`, `landState.ts`, `gitLand.ts`

Landing has no view of its own and is split by what each part is a *property of*.

`GitLandRow.tsx` draws the act inside the expanded Map row of the worktree it acts on: the Land button, that request's live state, a Cancel, and what stopped it last time including a conflict's paths.
It draws **nothing Project-wide at all**, because a row is repeated once per worktree and a Project-wide fact drawn there is drawn N times.
A row that cannot land names the blocker and *opens the strip* instead of drawing a second copy of its control.

`GitLandBar.tsx` is that strip, at the head of the map: one always-readable summary line (`landingSummary`) plus a disclosure holding everything Project-wide.
Behind the disclosure: the Project's verification command with its source, approval, recorded plan and in-place editor; agent authority; and the queue in the order the pipeline will reach it.
The queue is oldest first, because the daemon lists newest-first for history reads and the request about to run would therefore sit at the bottom.
It opens itself only while landing is blocked - the install stop is off, or the bytes are unapproved - and its install-stop `GrantGate` renders *outside* the disclosure, because a gate hidden behind a summary is the same defect as a surface rendering empty.

`landState.ts` owns the two daemon reads both parts share, mounted once by `GitTab.tsx` so the row and the strip cannot disagree about one request and so Log and Provenance pay for no poll.

`gitLand.ts` is the defensive parsing and the vocabulary:

- An unparseable gate response reads as *unapproved* rather than defaulting green.
- A progress reading attaches only to a `verifying` row.
- A step total that is absent, malformed, or below the step already reached becomes `null` rather than a number, because a wrong total is the one failure that makes a progress reading worse than none.
- `verifyProgressLabel` has exactly three forms (`step k of N · name · elapsed`, `step k · name · elapsed`, `elapsed · N lines`) and **never a percentage**, asserted rather than trusted in `test/gitLand.test.ts` and `test/renderer/git-land.spec.ts`.
- A `waiting` row takes the idle tone rather than the warn one, so a normal hold does not train the operator to intervene.

## History

`HistoryBrowser.tsx`, `TranscriptToolCalls.tsx`

Filters, transcript review with shared transcript labels and timestamps, a default-off tool-name/input disclosure with no results, a non-shrinking responsive action bar ahead of bounded run metadata, run-filtered Git provenance, long-message folding and per-message copy, and backfill progress and actions.
A held-conversation marker replaces Resume when a live CLI process still owns the row's conversation.
"Resume later…" starts nothing and hands the conversation to the Schedule tab, so it stays offered even while the row is held - a schedule fires later, when it may not be.

## Work lineage

`lineageView.ts`, the Work lineage section of `HistoryBrowser.tsx`

Pure wording for one lineage edge read from the entry it sits on: which direction it points, what the relation did in that direction, the far end's label, and where a branch was cut.
It recomputes nothing - names and existence come decorated from `GET /api/lineage`, because only the daemon can see live sessions, History rows, and deleted rows at once.
An unrecognised relation renders as itself rather than vanishing, since the set is the daemon's and can grow without this table.
A deleted far end renders as removed and loses its click rather than becoming a dead link.

## Branch point picker

`BranchPicker.tsx`, `branchPoints.ts`, `branchSeed.ts`

Choosing where a conversation forks.
It is a surface of its own rather than buttons added to the Transcript tab, which is deliberately inert - copy is its only verb - because branching starts a session and a stray tap on a reading surface must not.
The rail's Branch opens it for a harness whose published `branch_from_message` is set, and forks on the click for one whose is not, since a picker there would offer a choice the daemon refuses.

`branchPoints.ts` is pure and deliberately recomputes **no** eligibility: whether a cut is legal depends on the provider's own rule about unanswered tool calls and only the daemon has read the transcript, so the browser renders that answer and its reason rather than a second copy that would drift.
It owns the newest-first ordering, the opening selection (the newest point whose own default cut is available), the bounded one-line preview, and the sentences for each ineligibility and each empty reason.
An ineligible row keeps its reason inline, because the reader can see the message and hiding why it is not offered leaves them guessing.

`HistoryBranchPoints` is the same listing read from a History row rather than a live pane.
It is its own type, because conflating the two identities is how a caller sends a history id where a session id was meant, and it is what the Schedule tab's fork picker reads.

`branchSeed.ts` holds the prompt a `before` cut excluded until the pane that should carry it finishes replaying.
`TerminalPane.tsx` claims it there and inserts it into the composer without submitting, since re-sending it unedited would repeat the request the branch existed to change.
It is taken rather than read, so a reconnect cannot re-insert it, and bounded so a pane that never opens cannot grow the map.

## Transcript reader

`TranscriptTab.tsx`, `TranscriptToolCalls.tsx`, `transcriptView.ts`

An inert conversation reader.
It reads `/api/sessions/{id}/transcript` on open, on rollover, on the observed-user `mux:transcript-changed` event, and on the assistant-boundary `mux:turn-ended` event, never on a timer.
It offers local literal search plus per-message and whole-conversation copy.
A default-off toggle replaces count-only tool seams with individually collapsed native call names and inputs; results, telemetry, and persistence remain outside this surface.

There is no insert, no send, and no `onDone`: a stray tap here must not become a message, and on mobile `onDone` would close the drawer after every copy.
`App.tsx` owns the capability-gated pane-header chip shared by desktop and mobile, and focuses the named session before opening the Transcript drawer tab.

Search filters and highlights the loaded messages without changing copy-all, and its temporary scroll position restores the normal reading place on clear.
Each message shows the shared full local date-time label, and each per-message copy control uses a message-bounded sticky anchor.
Explicit Show more state persists device-locally by session, agent run, and stable message id in a 500-entry registry that stores no transcript text; search-only expansion is not saved.

### Partial copy

Partial copy runs through `SelectSheet`, a `useModalFocus` overlay holding the source text in a read-only `<textarea>` with `transcriptSelectedSlice` behind Copy selection, opened per message and for the whole conversation.
It exists because a handle drag across the column cannot work: the messages sit in a nested `overflow-y:auto` scroller, and once the anchor handle scrolls out of view Chrome re-derives the selection base from a stale screen coordinate and extends over every message above.
That is the browser's touch-selection controller, so no DOM or CSS change fixes it.

The chrome's `user-select:none` rule stays for its other half: it keeps speaker labels, timestamps, seam counts, and control labels off the clipboard for in-column drags, with the search input opting back in and the sticky controls hiding on touch while a selection is held.
A live selection over the body is read from `selectionchange`, the only event a native selection-handle drag produces, and suspends the follow-scroll until it collapses, at which point the bottom-follow predicate is re-evaluated against the column's actual metrics.

`transcriptView.ts` owns both event names, timestamp formatting, tool-input formatting, the bounded expansion model, the search matcher and highlight splitter, the bottom-follow predicate, the structural selection-active rule, and one-slot scroll memory, which has to outlive the host unmounting the body on each tab switch.
Copies bypass the clipboard ring (`withoutClipboardCapture`), since kilobyte replies would evict the snippets it exists to hand back.
