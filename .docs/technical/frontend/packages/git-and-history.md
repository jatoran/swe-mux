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

`GitLandRow.tsx`, `GitLandBar.tsx`, `landState.ts`, `gitLand.ts`, `landSetupPrompt.ts`

Landing has no view of its own and is split by what each part is a *property of*.

`GitLandRow.tsx` draws the act inside the expanded Map row of the worktree it acts on: the Land button, that request's live state, a Cancel, and what stopped it last time including a conflict's paths.
It offers **only** Land: a verify-only run is an agent surface (`request_verify`), and an operator with a worktree open has a terminal in it, so the row renders such requests without being able to start one.
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
- `landGateNote` draws **only** a skipped gate, on the row, in the strip's queue and history, and on the summary line while it runs.
  A full gate gets no note, because the states already narrate it and a chip on every row would bury the one that matters; neither a documentation-only land nor a reusing one has such a state, going from merging the trunk straight to fast-forwarding.
  The two skips read differently on purpose: one means nobody has ever run this content through the suite, the other means this queue ran exactly it, and a reader deciding whether to trust the row needs them apart.
- An unrecognised `verify_gate` parses to `''` rather than to a gate that ran, so no value this build does not know can render as "nothing verified this".
- `landKindNote` draws **only** a verify-only request, beside the branch and *before* the states it qualifies, for the mirror-image reason: a verify-only row moves through `Merging trunk` and `Verifying` in a landing's own words and stops one step early, which is when nobody is still watching.
  A land gets no note, and an unrecognised `kind` parses to `land` - a verify-only run drawn as a land under-claims, while a land drawn as a verify-only run would tell a reader a trunk did not move when it did.
- `landAttentionRow` is the supersession rule: a handed-back or refused row stops speaking for the summary once a **later** request for the same branch reaches a state that answered the branch, because nothing ever closes the old row and the redo is a new id.
  `verified` counts among those states, because the redo loop a handback asks for often runs through a verify-only request first, and leaving it out would reproduce the same defect one request kind over.
  `cancelled` does not supersede - withdrawing a re-request is not an answer - and ties do not either, so a bounce keeps asking for attention unless something demonstrably followed it.
  It is derived at the reading rather than written back, because `land_events` and the history disclosure are an audit that must keep saying the handback happened.
- `recentLandings` is what an idle summary says instead of the stalest historical row, and it is a floor rather than a total: `landed` only, a 24-hour window, over the newest 100 rows the daemon returns.
  `verified` is not counted, because nothing moved and the line says *landed*.

`landSetupPrompt.ts` is the copyable prompt the strip offers for setting verification up in *another* repository, shown in a collapsed disclosure beside the editor rather than only copied.
It is a frontend template because every fact in it is a property of the land queue's design rather than of an install, and the one variable is the script convention's name the gate payload already carries.
Its final paragraph - the agent cannot approve what it wrote, a human presses approve here, an edit un-approves by construction - is asserted by both test layers, because without it a copyable setup prompt reads as an agent setting up its own gate.

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

`TranscriptTab.tsx`, `TranscriptToolCalls.tsx`, `transcriptView.ts`, `transcriptAudio.ts`

A write-nothing conversation reader.
It reads `/api/sessions/{id}/transcript` on open, on rollover, on the observed-user `mux:transcript-changed` event, and on the assistant-boundary `mux:turn-ended` event, never on a timer.
It offers local literal search plus per-message and whole-conversation copy.
A default-off toggle replaces count-only tool seams with individually collapsed native call names and inputs; results, telemetry, and persistence remain outside this surface.

There is no insert, no send, and no `onDone`: a stray tap here must not become a message, and on mobile `onDone` would close the drawer after every copy.
The rule bounds *what a tap can reach*, not the number of buttons - copy, select, and the per-message read-aloud markers all leave the conversation, the PTY, and the session untouched (`../../../design/features/ui.md`).

### Per-message read aloud

Each assistant message carries two markers, one per kind, backed by `transcriptAudio.ts`: a pure index of the session's clips keyed on `message_anchor`, and the four states a marker can be in (`none`, `generating`, `ready`, `failed`) with only `ready` rendering as a play button.
A ready marker **plays** rather than regenerating, which is what the anchor buys - the daemon answers a repeat request for the same (run, message, kind) out of the store instead of spending a second summary call (`../../../design/features/voice.md`).
A `synthesizing` row reads as `generating` even though this tab did not ask for it, since the automatic path may be making exactly that clip; a request this tab issued and has not seen land is local state, because a clip another device is generating is invisible here until it arrives.
The index is refetched on `mux:voice-clip` rather than polled, and dropped on a rollover with the transcript it belonged to.
Markers are drawn only while `tts_enabled` is on: this is a per-item surface repeated once per reply, so it carries no gate and the master switch's gate lives in the voice panel's `tts` tab.
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
