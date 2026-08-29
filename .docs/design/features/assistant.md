# The Mux assistant

## What it is

A conversational operator for the whole workspace: the control plane given a chat surface and, behind the voice grammar's fallback tier, a voice.
It is not a coding agent and not an observer.
It never writes code, never touches a PTY directly, and never acts on its own initiative.
It converses, reads the fleet through existing read models, and drives existing daemon operations behind a per-class trust policy.
Asked for something that is coding, it routes: queue a message to an existing session, or spawn a new one.

## Contract

- **Fallback tier, never the reflex path.**
  The deterministic voice grammar (tier 1) and the fuzzy pass (tier 2, `voiceFuzzy.ts`) run in the client; only a wake-word utterance neither matched reaches the assistant.
  A spoken command's latency never includes a model call.
- **The model never emits an identifier and never executes.**
  Tools take project and session *names*; `resolve_session`/`resolve_project` map them onto live entities and answer ambiguity with candidate lists the assistant reads back.
  Session names are **display names**: the snapshot, `session_detail`, restatements, and resolution all apply the same rule every UI surface does (`session_titles.py` — a generated title wins while the session is auto-named), so the assistant never quotes a spawn id at a session the operator knows by its title, and a title it quotes always resolves back.
  Every side effect travels an existing path: the prompt queue, the spawn contract, the PTY interrupt operation, the graceful end operation, project-note writes.
  `NON_OVERRIDABLE_REASONS` and the approval floor therefore bind structurally, not by prompt.
- **Trust is enforced daemon-side per action class**, in `AssistantService._run_tool`:
  - *read* (session detail, transcripts, history search, note listing and reads, queue state): executes silently.
    **A read executing silently still has to be bounded.** `search_history` scans the whole
    archive and holds the single history executor thread while it does, so an unbounded one
    starves every other history read: measured 2026-08-23 on a 2.79 GB database, minutes of
    pinned thread with `/api/sessions` timing out while `/api/health` still answered instantly -
    the event loop was fine, the database thread was not. It now runs under a wall-clock budget
    (`ASSISTANT_HISTORY_SEARCH_BUDGET_MS`) enforced *inside* SQLite by a progress handler, which
    is the only lever that reaches a statement already running; exceeding it returns a tool
    result the model can narrow and retry from, never a raised turn. An empty query is refused
    outright rather than bounded, because there is no useful answer to narrow towards. The tool
    description says what the tool is *for* - locating a conversation you cannot already name -
    because the incident began with "summarize the audit session's latest response" becoming a
    full-archive search instead of a `read_transcript` on the session the operator had named.

    **A bounded read says that it is bounded.** `read_transcript` used to cut every message to
    2000 characters and drop the page's own `has_more`, both silently, so the model could not
    tell a whole message from the front of one, could not know older messages existed, and had
    no parameter to ask for either. That is not a smaller answer, it is an answer the reader
    cannot calibrate: observed 2026-08-23, an audit's final recommendations were cut mid-list,
    the assistant summarized the visible part, and then proposed *writing to the agent* to ask it
    to restate them - which is exactly what a reader does when its source stops mid-sentence.
    A cut message now carries `truncated` and `total_chars`, `has_more`/`next_before`/
    `abandoned_messages` are surfaced, and `chars` (up to `TRANSCRIPT_TEXT_MAX_CHARS`) and
    `before` let the model act on both. The cursor is the page's own anchor round-tripped as an
    opaque string rather than an index, because an index drifts the moment a live conversation
    grows between two reads; a `before` that is present but unusable is an error rather than a
    silent fall back to the newest page, which would loop the model through one page while it
    believed it was paging backwards. `search_history` marks its cut summaries the same way,
    where the remedy is to read the session it names rather than to ask for more characters.
  - *navigation* (`run_ui_command`): dispatched to the operator's device (below), no confirmation.
  - *reversible* (queue an inert draft, write to a project note — `write_project_note`, below — spawn a session, create a project (`create_project`, below), or stage unsent composer text with `type_into_session`): follows `assistant_trust_reversible` — `auto`, `cancel_window` (default: announce, execute after ~6 s unless cancelled), or `confirm`.
  - *consequential* (armed send, interrupt, end session, `submit_session_composer` — pressing Enter on staged composer text is a send — and `run_project_action`, because a build or a deploy is not taken back): always an explicit confirmation with a bounded TTL; this floor is deliberately not configurable.
  A pending or scheduled action is typed state (`assistant_actions` row) rendered as a card, and a daemon restart expires anything still pending — a confirmation minted by a dead daemon can never execute.
- **Dialog state is daemon-owned** (`assistant_dialogs`/`assistant_messages`/`assistant_actions` in SQLite, one worker thread like `voice_clips`).
  Any device resumes the same conversation; a dropped tab cannot orphan a half-confirmed action.
- **Freshness is computed by the system, never self-assessed.**
  The per-turn workspace snapshot (`fleet_snapshot`) carries ages derived from session records; `state_since == 0` reads as unknown, never as "just now".
  A session whose harness handed off to background agents carries `running_work_for` beside `state_age`, because `idle` with no `turn_running_for` is also the shape of a session an hour into a request, and answering "how long has that been going" from `state_age` alone reports the hand-off instead of the work (`features/status-detection.md`).
- **Budgeted like every model feature.**
  Calls run on the configured OpenRouter model (`assistant_model`, default `openai/gpt-5.6-terra`; tool calling verified against the live catalog), spend lands in the shared automation ledger under `builtin:assistant` (tokens, cost, and cached prompt tokens per call), and the daily budget is checked before each call — an exhausted budget fails the turn closed.
- Failures are typed `AssistantError` and never touch PTY, session, transcript, history, or project state.

## The turn

`POST /api/assistant/dialogs/{id}/turns` records the user message and returns `202 {turn_id}`; everything else arrives over the ordinary event stream so every connected device renders the same turn:

`assistant_turn_queued` (only when a turn is already running) → `assistant_turn_started` → `assistant_sentence` (per sentence, **dual-form**: `display` and separately paced `speech`) → `assistant_tool_status` / `assistant_action` as tools run → `assistant_turn_done` (full display, `exhausted`, and usage) or `assistant_turn_failed`.
`assistant_message` is emitted outside that sequence, when an action resolves.

The loop behind it makes at most `MAX_MODEL_CALLS_PER_TURN` model calls per user turn, appending tool results between them.
The prompt is ordered by how often each part changes, most stable first, because a provider's cache can only reuse an unchanged prefix: the fixed short-response primer, the device's command vocabulary, the focused Project's note, the last `assistant_context_messages` dialog messages, the live fleet snapshot with any action still awaiting an answer, and the operator's text.
Interrupt cancels the running task; nothing already executed is undone.

- **The sentence events are the reply, not a preview of it.**
  With `assistant_stream_replies` on (the default), `openrouter.complete_tools` streams and the daemon releases each sentence as the model writes it, so a device speaks the answer while the model is still generating.
  Splitting happens daemon-side because a token delta is not a sentence and half a sentence is not speakable; the boundary requires the whitespace *after* the terminator to have arrived, which keeps "3.5" and "e.g." intact, and `STREAM_SENTENCE_MAX_CHARS` bounds how long unpunctuated prose can delay the first sound.
  Streaming is a latency optimization and never a capability the reply depends on: a provider that rejects the streaming parameters is answered unstreamed, and the sentence events are emitted either way, so the client has one path to speak from.
  The one thing streaming may never do is retry after delivering text - it has been spoken, and a second attempt would say it again.
- **Everything after a card opens is display-only.**
  A tool returning `pending_confirmation` sets the turn's speech suppression: subsequent `assistant_sentence` events carry `speech: ""` and `speech_suppressed: true`, and `assistant_turn_done` carries `speech_suppressed` plus a `speech` field holding only what still needs saying.
  The card is the spoken statement and the model's paraphrase of it is the same sentence twice.
  This is structural rather than prompted, because a model that ignores the instruction still must not double-speak.
- **A resolved action is a transcript row, and only what is still open rides the context.**
  A confirmation is a button or a spoken word, never a turn, so the message log alone could not record that the operator said yes; the model read its own unanswered "say confirm" and proposed the write again.
  Resolution now writes an `action` row into `assistant_messages` (`action_transcript_line`, keyed by `action_id` under a partial unique index, so the race between a cancel window firing and an operator answering it cannot produce two).
  The panel draws it in the position it resolved in, which is the record the operator lost when a card stopped being open, and `replay_dialog` folds it into the assistant message it followed for the model.
  The fold is what keeps the dialog portable: only `user` and `assistant` reach the wire, because a bare mid-conversation `system` message is rewritten differently by every provider and a replayed `tool_call`/`tool` pair needs an id pairing some providers police - either would turn "run this on a different model" into a compatibility bug.
  Read and navigation actions are excluded: a read is already in the turn's own tool results, and a UI command is steering the operator is watching happen.
  What remains in the live block is only what is *still* awaiting an answer (`_open_action_ledger`), because that is the one thing no transcript row can carry.
- **A turn has a round budget, the model is told it, and running out is reported.**
  `MAX_MODEL_CALLS_PER_TURN` is a runaway guard, not a work budget, and at six it was smaller than an ordinary multi-target request: "open three sessions and stage a note in each" needs a read, three spawns and a reply, and the turn stopped mid-way having said only "Ready when you are" (measured 2026-08-20).
  Three things changed together, because raising the ceiling alone would only move where it stops silently.
  A trailing system line states the rounds remaining and asks the model to batch independent calls into one response rather than one per round, and not to re-read what a tool already returned; it is replaced each round rather than appended, so exactly one budget is ever in the prompt.
  Below `MODEL_CALL_WARNING_ROUNDS` that line changes to "start no new work and say what is done", so a turn lands on a sentence instead of on the ceiling.
  And exhausting the rounds appends a plain notice to the reply, marks `exhausted` on `assistant_turn_done`, and logs a warning — the notice is spoken even when speech is otherwise suppressed, because a half-finished turn is the one thing the operator must hear.
- **The prompt is ordered by volatility, and that ordering is the cache strategy.**
  A provider can only reuse an unchanged *prefix*, so one line that differs per turn ends the reusable region there and re-bills everything below it.
  The workspace snapshot differs by construction - its ages are recomputed on every call - and sitting second it capped every possible hit at the primer, however much identical text followed.
  So the order is: primer (identical on every call this assistant makes), the focused Project's note (turns over on a Project switch), the device's command vocabulary (turns over when the fleet or the focused pane does), the dialog window (append-only), the live snapshot plus open actions, and the operator's text.
  The note precedes the vocabulary because it changes less often - a command's `available` flag flips with the focused pane - so the more frequent event still leaves the note cached.
  Being last is also where the live snapshot belongs on the merits: it is what the question is about, and it now sits beside the question rather than eight thousand tokens above it.
  The round-budget line stays trailing for the same reason.
  Two rules remain load-bearing: nothing may be inserted ahead of the primer, and no stable block's text may be interpolated per turn.
- **A breakpoint is marked on every stable block, and on every route.**
  Anthropic and Qwen cache nothing without an explicit `cache_control` marker (`EXPLICIT_CACHE_CONTROL_PROVIDERS`), but OpenRouter *translates* markers across providers - an Anthropic-style block reaches a supporting OpenAI model as a `prompt_cache_breakpoint` - so a marked prompt is understood wherever it routes and `marks_cache_breakpoints` gates on the endpoint rather than on the model.
  A custom endpoint is the one place nothing is sent, because it has no translation layer in front of it (`cache_policy="unknown"`), and no implicit hit is assumed there either.
  All three stable blocks are marked rather than only the primer: Anthropic reads a cache at a breakpoint and allows four, so switching Project still hits through the blocks ahead of the one that changed.
- **A cache is pinned to a provider instance by `session_id`, or it cannot be read back.**
  OpenRouter load-balances across instances and a cache lives on the instance that wrote it, so two calls seconds apart inside one turn land together and hit while the first call of the next turn lands somewhere cold.
  Measured before the fix: every second call of a turn reported the full prompt cached, every first call reported zero, forty seconds apart, on a byte-identical 4k prefix.
  `apply_session_routing` sends the dialog id as OpenRouter's sticky-routing key (documented ceiling 256 chars, stickiness expiring after ten idle minutes) and switches on full usage accounting in the same place, for OpenRouter endpoints only.
- **Cache reads, cache writes, and the signed discount are all recorded, because reads alone mislead.**
  `cached_tokens`, `cache_write_tokens`, and `cache_discount_usd` from each call's usage payload land in the spend ledger beside the input and output counts - per call, not per turn, because the first round of a turn writes the cache and the rest read it.
  Reads and writes are disjoint subsets of the input tokens and neither is added to them.
  The write count exists because a run that writes on every call and reads on none reports 0% cached and is indistinguishable from a run with no caching at all - while costing 25% *more* per prompt token, since GPT-5.6 and Anthropic bill a write at 1.25x input.
  The discount is signed for the same reason: negative is that premium, unread.
  A zero read count stays deliberately ambiguous ("no hit" and "this provider reports no caching" alike), which is why it is recorded rather than asserted from; a `None` discount is unmeasured, which is not the same as zero.
- **Speech suppression is for a card that *is* the whole turn, not for any turn with a card.**
  Suppressing whenever a card opened also swallowed "I opened two of the three and one needs your confirmation" — information the card cannot carry.
  The rule counts: exactly one card opened and no mutation executed.
- **An utterance that arrives while a turn is running is queued, never refused.**
  Refusing it (`"a turn is already running in this dialog"`) left the client holding text with nowhere to put it, so speaking over the assistant lost what you said and you repeated yourself; it is also how one sentence came to be split across two dialogs, the first fragment refused and the rest opening a new conversation.
  At most one turn waits per dialog and consecutive arrivals **merge into it**, because a thought finished in two breaths is one request.
  The queued turn keeps the id its `assistant_turn_queued` event announced, so the words render once and the later `assistant_turn_started` updates that same bubble.
  The queue drains even when the turn ahead was cancelled or failed — an interrupted turn is usually interrupted *by* the thing now waiting.
- **`spawn_session` carries two prompt parameters, and the split is submit versus stage.**
  `seed_text` is a prompt the new agent RUNS: it rides the CLI's argv, so it is submitted by construction and can never leave text waiting for review.
  It was first documented as staging without sending — a description written from the field's summary rather than its delivery path — and the model followed it faithfully: three sessions opened with their prompts already submitted while the operator asked for them left unsent, and the model told them "none of the messages will be sent" (2026-08-20).
  `stage_text` is the real stage-without-send: the daemon spawns, waits for readiness, and writes a bracketed paste with no carriage return (`_stage_spawn_text`, `interfaces.md`), so it works headless with no mounted pane.
  The primer and both schema descriptions state the split, the confirmation card says "prompt staged unsent" or "running the prompt", and the tool result carries `staged`/`submitted` so the model reports truthfully which happened.
  The two are mutually exclusive at every layer (assistant preflight and `SpawnRequest.parse`).
  `type_into_session` also stages text but needs the session's terminal already mounted on the device, so it is the wrong tool immediately after a spawn.
- **`spawn_session` also carries the model, and an unusable one is a sentence rather than a dead pane.**
  "Open an opus session in X" passes `model` in the harness's own spelling; the daemon owns the mapping to that CLI's argv (`launch-profiles.md`, `backends.md`), so neither the assistant nor the browser ever names a flag.
  The check happens in preflight, before a card exists, because the failure it replaces is a pane that appears and dies with the flag echoed back at it: a name the harness would not recognize is refused there, naming the vocabulary that would work instead.
  Which names those are is per harness and differs sharply between them (`backends.md`): two of the five fuzzy-match anything model-shaped, one takes `provider/model` only, and two have real namespaces.
  Preflight also rewrites the argument to the canonical spelling and **pins the harness it validated against**, so the card the operator confirms and the launch they get cannot differ - "opus 5" is restated and spawned as `claude-opus-5`.
  Pinning happens only when a model was asked for; an ordinary spawn still falls through the daemon's full default chain, which reads the Project's committed configuration this layer cannot see.
  A model asked for in a Project with no default harness is answered by asking for one rather than validated against a guess, because a guess would make the card name a CLI the spawn might not pick.
  The card says the model whenever one was asked for, spoken form included: it is the difference between the session the operator wanted and an ordinary one.
- **An identical proposal is answered with the existing action, never a second card** (`_duplicate_action`).
  A pending or scheduled duplicate is refused for every kind: two cards for one intent means answering either leaves the other armed.
  An already-executed duplicate is refused only for `DUPLICATE_GUARDED_KINDS` - note writes, project creation, queued messages - where repeating is itself the damage; spawning two identical sessions is something operators genuinely ask for.
  The fingerprint is the kind plus the *resolved* arguments, so two differently-worded proposals for the same write collide.

## UI command dispatch

Focus, drawer tabs, and panels are per-device UI state the daemon cannot run.
The `run_ui_command` tool records a `dispatched` action and waits (bounded) for a device acknowledgement; the client executor resolves the phrase with `planUiCommand` (`uiCommand.ts`) — registry aliases first, then the closed query grammar (which owns "open project X" navigation and answers entity misses with candidates), then the fuzzy pass, then an exact label match — runs the plan, and reports `POST /api/assistant/actions/{id}/ui-result`.
The `{text}` catch-all is excluded from that ladder by construction: for a dispatched command it matches anything, and it once turned "move to project X" into a voice lookup instead of a failure the assistant could react to.
The per-turn context also names the reliable command shapes ("open project <name>", "open the <tab> tab", …) so the model prefers them over free paraphrase.
No connected client is an honest tool failure, not a silent success.

## Client-executed terminal work

Three more kinds execute on the operator's device, because the mounted pane owns PTY
writes (bracketed paste, replay, ownership claims, acknowledged results) and pane
placement is per-device layout state — the daemon never types into a PTY for the
assistant and never picks a pane:

- `type_into_session` stages text in a session's composer **without** a carriage
  return, via the same `insertIntoTerminal(…, submit=false)` primitive voice "append"
  uses; repeated calls accumulate, and nothing reaches the agent. The session's
  terminal must be mounted on the device — an unmounted pane reports an honest failure
  the assistant relays ("focus it first").
- `submit_session_composer` presses the same Enter the mobile Send control uses
  (`sendKey('\r')` through the pane), sending whatever is staged. It is a send, so it
  sits on the consequential always-confirm floor.
- `spawn_session` from a turn with a connected workspace dispatches to that device's
  own launch path (`spawnTerminal`), so the new session opens as a **tab in the
  currently active pane** with the optimistic leaf and focus every other launch entry
  point gets. There is
  deliberately no daemon fallback when the dispatch fails: a lost acknowledgement plus
  a daemon retry would spawn twice. A turn with no `client_id` (old client, headless)
  keeps the daemon `spawn_op` path - which is no longer the difference it was: a
  daemon-started session is joined to the layout by the client's own reconciler, also as
  a tab in a sensible pane, but deliberately without taking focus
  (`workspace-layout.md` § Placement and persistence). Dispatching to the device is
  still the better path for a spawn the operator just asked for out loud, because that
  one *should* land in front of them.

Every client-executed action is stamped with the originating tab's per-tab
`client_id` (sent in the turn's `client_context`, persisted in the action's
arguments so a later confirm still targets the same tab); executors on other devices
ignore it — an untargeted broadcast would type into every mounted copy of a pane and
spawn one session per open workspace. Mutation rows keep their persisted status; a
synthetic `dispatched` `assistant_action` event carries the work (with
`target_session_id`/`project_id` extras — `session_id` is a first-class MuxEvent
field the bus lifts out of the payload) and the device reports back through the same
`ui-result` endpoint UI commands use.

## Writing notes

`write_project_note` is the only note-write tool - it replaced `append_project_note` and `edit_project_note`, whose split taught the model a distinction operators do not make.
The transform is `apply_note_write` (pure, tested); the daemon closure supplies the note inventory, the current revision, and the `note_changed` event other devices refresh on.

**`top` is the default and it means under the note's leading heading run**, not byte 0.
The scanner consumes a contiguous run of ATX headings from the start of the body - blank lines between them are fine, anything else ends the run - so `# swe-mux Notes` followed by `## Unsorted` is one preamble and a dictated note lands beneath both.
A heading with a paragraph under it is a section boundary, not preamble.
Fenced code is tracked while scanning, so a `#` line inside a pasted shell transcript is a comment rather than the note's structure - a mis-detected fence would make some pasted `# comment` the note's title and write into the middle of a code sample.

A body that opens with prose usually has a lead paragraph to respect, and the write goes above it rather than inventing a structure.
The exception is a **buried title** (`_stranded_title`): the swe-mux note this feature exists for opens with three dictated items sitting above `# swe-mux Notes`, because the old `prepend` wrote to byte 0.
Respecting that as a lead paragraph would stack every new write on the damage forever, so a level-1 heading within `NOTE_TITLE_SEARCH_LINES` of the start, with nothing but non-heading text above it, counts as a title that got buried and `top` goes under it.
The level and distance bounds are the whole guard: a `## Later` near the bottom of an all-prose note is a section following an introduction, not a title, and does not fire.
Existing strays are skipped, never moved - the tool writes, it does not reorganize.

The other positions: `section` writes under a named heading (resolving case-folded, exact matches winning over substrings, and refusing ambiguity the way `replace` refuses a non-unique find); `after`/`before` sit beside a unique `anchor` span; `at_line` makes the text *become* a 1-indexed line; `replace` swaps a unique `find` span.
Every position except `at_line` normalizes the seam to exactly one blank line on each side so a dictated paragraph never glues onto the next; `at_line` is deliberately exact, because the model picks that number off the numbered view and the number has to mean what it says.

**`end` exists but is never inferred.** "Add", "jot", "note this down" and "append" all mean `top` - a note is a stack of things you thought of, and nothing is ever pinned to the bottom of one.
Nothing at the tool layer can verify what the operator said, so the guard is legibility rather than validation: the schema and system prompt both say `end` requires an explicit request, and `restate_action` writes "at the very END of" into the card **and** into the spoken announcement.
The spoken form drops the text preview for latency but keeps the position, because that is the detail the operator would otherwise have to undo by hand and the cancel window is only useful if the announcement names it.

Every turn carries the focused project's primary note as **numbered lines plus its heading outline** (`_note_context` → `note_page`/`note_outline`, first `NOTE_CONTEXT_LINES`).
That is what makes "jot this down" one tool call: without it the model either burns a round trip reading the note or writes blind, and writing blind is how text ended up above the note's own title.
The tail is addressable rather than truncated into silence - the context names the `read_project_note from_line=…` that pages further down, and that tool returns numbered lines and the outline too.
Scoped to the focused session's project, or to the only project when there is exactly one; guessing among several would hand the model an outline for a note the operator did not mean.
A missing or unreadable note is swallowed to a debug log: context assembly never fails a turn.

## Creating projects

`create_project` mints a project that does not exist yet — the one assistant mutation that touches the filesystem — and its whole safety story is one constraint: **the model supplies a name, never a path.**
The folder leaf is derived from the name by the same deterministic normalization the Add-project dialog suggests (`leaf_names.suggest_folder_name`, spaces → hyphens), validated under the shared Windows-safe leaf rules, and joined to the one configured parent (`new_project_parent`, Settings → Projects).
Unset, missing, duplicate-root, and existing-non-empty targets are all answered at preflight — the refusal names the setting — so a card never pends for something that cannot execute; adopting populated folders stays the Add-project dialog's job.
The restatement carries the exact absolute path (and, when the root matches a tombstoned registration, that the removed project's identity and history revive), so the operator confirms what lands on disk rather than a name to resolve.
Execution is the ordinary registration path (`ProjectManager.register` with `create_missing`, emitting the same `project_created`/`project_restored` events as `POST /projects`); setup commands never run from the assistant — the result says so and points at the Run menu.
An optional `git: true` chains the one-time repository initialization with its contract intact: nothing staged, no commit made, and an init failure reports without unwinding the registration.
Reversal is the same as spawn's class implies: removal is a registration tombstone that deletes nothing on disk, and the minted folder is empty.

## Running project actions

The assistant reaches the Project Run menu through two tools, and the hard part was already built (`project-actions.md`): `run_action` executes only the exact bytes a human approved, so the assistant inherits that boundary whole and adds no authority of its own.

- `list_project_actions` names a Project's actions with what each is for, how many terminals it opens, the inputs it declares, and **its own approval state** - trust is per source file, so one unapproved file leaves the rest runnable and a single "approved" flag for the Project would be a lie.
- `run_project_action` starts one. It is **consequential**, not reversible: a build, a deploy, or a migration is not undone the way removing a project registration or clearing a composer is, so it sits on the always-confirm floor rather than under `assistant_trust_reversible`, where `auto` would run repository commands with no card at all.
- An unapproved action is refused **at preflight, naming the file a human must review**, exactly as the MCP surface does. Nothing pends for something the executor would refuse, and the refusal is the only useful next step: neither the assistant nor the agent that wrote the action can approve it. A file edited between the card opening and the operator confirming it is refused again at execution, because the executor is the authority and preflight only refuses early.
- The card restates the action's **title**, its terminal count, and any **input values**, which are the one part of a run no approval covers (`${input:…}` is substituted at run time, so the approved template never contained them).
- Resolution is one implementation, `preview_action_run`: a spoken title, an id, or a fragment resolves to exactly one action, and a miss or an ambiguity answers with candidates rather than a guess. Input values are validated through `substituted_action` rather than a second copy of that rule.

**The outcome is a terse notification, never a read-back.** A step is an ordinary one-shot terminal, so its exit code arrives long after the turn ended and after the operator confirmed the card - there is no reply for it to ride. A bounded watch (`ACTION_OUTCOME_WATCH_SECONDS`) polls the step sessions and then says one sentence through `assistant_notice`: finished cleanly, or an issue flag when a step exited nonzero, when its output tail carries a failure marker, or when a step is still running at the bound. Reading output back was rejected at scoping as spam, and the flag never quotes the line that produced it - the tail is classified and discarded. `UNHEALTHY_OUTPUT_MARKERS` is deliberately narrow and strong ("traceback", "npm err!", "command not found"), because bare "error" and "failed" appear in healthy builds and a flag that fires on green runs is a flag the operator learns to ignore. A step whose session is gone reports as unknown rather than clean.

A re-run is deliberately **not** duplicate-guarded past execution: "run the tests again" is an ordinary ask, and every run already needs its own explicit confirmation. Two *open* cards for one run are still refused, like every other kind.

## Voice attachment

The assistant is text-first and voice-attached, not voice-only:

- In the voice overlay, a `talk`/`chat` mode toggle switches the same floating panel between the dictation draft and the conversation view (`AssistantPanel`); the chat is also reachable with the microphone off (`assistant.toggle`).
  **Chat is the default mode** (device-local, persisted; a deliberate switch sticks): the assistant lane is the primary one, and talk — free, deterministic, model-less — stays one tab away as the degradation path for budget exhaustion, provider outages, and verbatim dictation. Talk mode is deliberately not removed: the tier-1 grammar it carries is load-bearing inside chat mode too ("Mux, stop", confirm/cancel, navigation), and the assistant's composer tools execute through the same acknowledged terminal path.
  Chat mode is bounded to roughly half the viewport — a dialog consulted beside the terminals, never a takeover — and collapses to its header (device-local, persisted); the collapsed body stays mounted so streaming, card speech, and earcons keep working while folded.
- **Thinking out loud is not answered at every pause.** Three deterministic client mechanisms
  (all in `voice.md`): `voice_chat_patience_ms` lengthens the endpoint tail while the
  assistant is the addressee (commands keep short-circuiting it); a **completeness heuristic
  runs before the turn is dispatched**, so an utterance ending mid-clause earns a patience
  extension *scaled to how confident the rule is* instead of becoming a turn; and the `hold`/`proceed`
  brainstorm pair buffers plain speech until a "go ahead" cue releases it as one consolidated
  turn. Deliberately not an assistant tool: a wait tool runs *inside* a turn, so every pause
  would still cost a model call — the same reason confirm/cancel keeps the model out of the loop.
- **The model is never instructed to return nothing** - it is instructed to return a *token*.
  Incomplete fragments are handled *before* the model by the heuristic above wherever a word rule
  can see them, because a model told to sometimes withhold a reply will withhold one when it
  should have answered, and a model asked "are you finished?" is the round-trip spam the whole
  design avoids. But a word list only recognizes danglers, and "now I want you to add" ends on a
  transitive verb: it reaches the model as a turn. What the primer used to teach for that case -
  offer the brainstorm hold in one short sentence, while still answering - produced the failure it
  was meant to prevent, because there was nothing to answer, so the *entire* reply became
  "Go ahead, I can hold while you finish", spoken aloud, mid-thought. An offer phrased as an
  addendum silently becomes an interruption exactly when the fragment is most incomplete.
  So the primer now teaches the **hold sentinel** (`ASSISTANT_HOLD_TOKEN`, `[[HOLD]]`): when a turn
  reads as an unfinished thought *and there is nothing in it that could be answered*, the whole
  reply is that token and nothing else. Anything answerable is answered normally and holding is
  never mentioned - a partial answer beats silence.
  The token is plumbing and never reaches the operator. `run_turn` suppresses the sentence, the
  speech, and the stored message, emits `assistant_turn_done` with `held: true`, and returns before
  the "Done." fallback that a turn writing nothing would otherwise get. A reply that leads with the
  sentinel and then keeps talking is a primer violation, and is answered with the token stripped
  rather than suppressed or read aloud (`strip_hold_sentinel`), because `[[HOLD]]` spoken to the
  operator is the worst outcome available.
  Deliberately not a tool, for the unchanged reason: a wait tool runs *inside* a turn, so every
  pause would still cost a model call. The sentinel costs the one call that already happened and
  turns its output into silence.
- **A held turn is parked on the client, never re-sent.** `DeferralPen.park` keeps the operator's
  words with no release timer, and the next breath merges with them into one turn. That asymmetry
  is what makes a hold *loop* impossible rather than merely unlikely: submitting the fragment alone
  would produce the same verdict again forever, so nothing ever submits it. Bounded by
  `DEFERRAL_PARK_MAX_WORDS` (400, past which the accumulated text is a turn in its own right) and
  `DEFERRAL_PARK_MAX_MS` (120 s, so an abandoned half-sentence is forgotten rather than glued to
  whatever is said an hour later). The chat panel shows `unfinished · waiting for the rest`, and
  the follow-up window still opens so the next breath reaches the assistant.
  **The parked words stay on screen** in the panel's pending row (`voice.md`), which also carries
  the speculative decode's provisional reading of the breath in progress - so the operator sees
  what they said seconds before the accurate transcript can exist, and a held fragment never
  appears and then vanishes. A held fragment is a client-local row rather than a dialog message
  precisely so that the merged turn can re-send it without the panel having to delete anything.
- **Queue-merge is the safety net under both.** A fragment the heuristic does not recognize
  becomes a turn, and the next breath merges into the waiting turn rather than opening a second
  one; barge-in already silences a reply to fragment one.
- **The mode toggle is the microphone's addressee switch.**
  While chat mode is open with Talk active, every plain utterance is a conversation turn and the dictation draft is deliberately deaf — the two modes never both hear the same speech.
  A wake-word utterance keeps its normal meaning in either mode ("Mux, stop" still kills playback mid-dialog), and the chat header shows `mic→assistant` while the routing holds.
- **With Talk active, a turn sends progressive text into one speech stream** (`assistantSpeech.ts`).
  The turn claims and opens an empty acknowledgement-only stream at `assistant_turn_started` - which halts the previous turn's audio, since a new question supersedes the answer the operator moved on from - and each `assistant_sentence` with speech appends one raw fragment.
  Append requests serialize only until the daemon acknowledges queueing, not until synthesis completes.
  The daemon receives later sentences while the opening clip encodes, then combines accumulated complete sentences into larger provider-neutral audio segments.
  `assistant_turn_done` closes the stream and seals its remaining text.
  Two invariants hold the design together.
  Everything one turn says shares one stream, including any card it opens, so nothing a turn says can cut off something else the same turn said: starting a second stream hard-stops the first, which is what used to truncate the card's line mid-word and follow it with several seconds of silence while the next clip synthesized.
  And append acknowledgements are serialized, because fragment order on the daemon is the order its `speak` calls arrive.
  The daemon's single stream worker owns audio segmentation and synthesis order; the assistant's sentence boundary is not an audio-file boundary.
  A third rule follows from the first two: `spoke` - the flag deciding whether `assistant_turn_done`'s text still needs saying - is set when an append is **queued**, never when its acknowledgement returns.
  The daemon is right to put the whole reply on the completion event, because a client consuming no sentence events needs it; not duplicating it is the client's half of that contract.
  Reading the flag after the post made the guard consult state the operation it guards against had not written yet, and a one-sentence reply said itself twice: the sentence, then the identical fallback, measured 2026-08-23 as two segments and 11.8 s of audio for 95 characters.
  It only ever missed on a short reply, because a one-sentence turn can end in the same tick as its sentence event.
- A **follow-up window** (~8 s after a spoken reply) routes the next wake-word-free utterance back to the assistant in dictation mode too — one addressee removes the ambiguity the wake word exists to resolve.
- **Starting a fresh conversation is a deterministic registry alias, not a model turn.**
  `assistant.newConversation` puts "new conversation", "clear context", and their variants (`NEW_CONVERSATION_PHRASES` in `assistant.ts`) on the ordinary command registry, so clearing context costs no model call and cannot be paraphrased into something adjacent.
  Nothing collides: the spawn aliases are `new <harness>`, never `new conversation`.
  It is the one assistant act that runs on the word with **no confirmation card**, and that is only safe because nothing is destroyed.
  `startNewDialog` merely *unremembers* the device's dialog id, so the daemon keeps the prior dialog in `GET /api/assistant/dialogs` and the panel keeps it readable under a collapsed `previous conversation` disclosure.
  The spoken reply therefore has to carry both halves, context cleared and previous conversation still there: "context cleared" on its own describes the same act as a deletion the operator can neither see nor undo, which is what would make the missing confirmation unsafe rather than merely absent.
  Both surfaces go through `startNewDialog`, which announces `mux:assistant-dialog-reset`, and the panel never clears itself directly - so a conversation started by voice and one started by the `new` button leave the panel in exactly the same state.
  The alias is unavailable while the assistant is off, and says so in the voice catalog rather than disappearing from it.
- **Spoken confirmation is deterministic.**
  A pending or scheduled card is spoken with the daemon-built `announcement`, which omits the text preview the visible card keeps; a bare `confirm`/`cancel` (a closed word set, `spokenConfirmation` in `assistant.ts`) resolves the newest open card directly against the confirm/cancel endpoints — the model is never in that loop, so it cannot "confirm" by talking about it.
  Anything conversational ("yes but change the wording") falls through to the model as an ordinary turn.
  The grammar is deliberately forgiving about *shape* while staying closed about *meaning*: filler and politeness are trimmed from both ends ("yeah, confirm that please", "mux, do it now"), and a cancel word anywhere in a short utterance beats an affirmative wrapping it, because reading "yes, cancel that" as a confirmation performs the action the operator was stopping.
  Every phrasing the set misses reaches the model as a fresh request and is proposed a second time, which is what "I confirmed and it asked me again" was.
- **An open card changes what the microphone is waiting for.**
  The chat patience that keeps thinking-out-loud from being answered at every breath (`voice_chat_patience_ms`) is dropped while a card is open, and a recognized verdict lets a speculative decode commit the same way a wake-worded command does — the operator is answering a closed question, not composing a thought.
  The real decode stays on the `dictation` profile: an answer that turns out to be conversational still has to be transcribed accurately, and the speculation already carries the latency win.
- **A card is announced exactly once, per card and per device, and its window moves exactly once.**
  This is the load-bearing rule, not an optimization.
  Six seconds is generous for a card that appeared on screen and too short for one being read aloud, so a device that speaks one posts `/announced` and the window restarts (`CANCEL_WINDOW_SPOKEN_SECONDS`, clamped to `CANCEL_WINDOW_MAX_SECONDS` from creation).
  Extending re-emits the card so its countdown stays honest — and a device announces a card when it sees one, which closes the cycle: emit, announce, extend, emit.
  It ran in production on 2026-08-20: eighty extensions about twenty-five milliseconds apart, each spawning its own speech clip, still speaking minutes after the operator had closed the microphone and killed only by killing the app.
  Two independent cuts now hold it open. The daemon extends a given action once (`_announced`, in memory because a restart expires every scheduled action anyway) and logs a warning on any repeat; the client announces a given action id once, because the unit is the *card*, never the event.
  Both fail safe: a client that never calls `/announced` keeps the original window, and the deadline only ever moves forward.
- **Announcing never takes the floor; a spoken verdict does.**
  A card joins the stream already speaking rather than starting one, so the sentence the operator is mid-way through hearing is not cut — an announcement that interrupted is what turned the repeat above into the same sentence restarting over and over instead of being said once.
  The deterministic `confirm`/`cancel` verdict is the deliberate opposite: the operator has just answered the card being read out, so finishing the question is worse than cutting it.
- Earcons (`earcons.ts`, WebAudio oscillator blips — no assets, no fetch) acknowledge the endpoint instantly and mark turn completion and pending actions, which is what makes 1-2 s of model latency feel attended rather than dead.
- **The conversation view is mounted exactly once, and its size is somebody else's business.**
  It lives in the app-level voice dock (`features/voice.md`), which draws it at full size, as a one-row peek, or not at all, by passing a `variant` — never by mounting or unmounting it.
  This is the client half of the once-per-card announcement cut: `announcedRef` is per-instance and in memory, so a remount is indistinguishable from a device that has never seen the card and speaks a scheduled card's line a second time.
  It used to be re-parented between the focused pane's overlay and a fixed top layer as focus moved, which remounted it on an ordinary pane change.
  Collapsing the dock to the top-bar chip therefore leaves the dialog live: turns arrive, replies are spoken, cards open, and the chip carries the open-card count and an unread mark.
  A card opening raises the dock to at least the peek row, where cards keep their buttons and countdown — a confirmation the operator cannot see is one answered by timeout.

## HTTP surface

- `GET  /api/assistant` — enabled, model, budget, spend, trust level, diagnostic.
- `GET|POST /api/assistant/dialogs` — list, create.
- `GET  /api/assistant/dialogs/{id}` — messages, actions, whether a turn is running.
- `POST /api/assistant/dialogs/{id}/turns` — `{text, client_context}` → `202 {turn_id, queued}`. `queued` means accepted and waiting behind a running turn, which is never a refusal.
- `POST /api/assistant/dialogs/{id}/interrupt`
- `POST /api/assistant/actions/{id}/confirm | /cancel | /ui-result | /announced`

## Config knobs (`config.py`)

`assistant_enabled` (off by default, like every model-cost feature), `assistant_model`
(**pinned**, not routed: the assistant is an agentic tool-calling loop, and a model that
only sometimes emits a well-formed call fails as a broken assistant rather than a cheap
one, so a blank value is a validation error rather than a fall-through to the routed cheap
model — it is edited in Settings → Voice → Mux assistant, with the assistant's other knobs,
and indexed from Settings → Accounts → Models),
`assistant_daily_budget` (the shared `{tokens?, usd?, mode}` spending shape, defaulting to
`$2.00` in `usd` mode - which is the unit it enforced before the shape existed; it takes a token
limit or first-hit instead, and against a provider that reports no cost the token axis is the
only one that can bind, per `design/features/budgets.md`),
`assistant_max_output_tokens`, `assistant_context_messages`,
`assistant_trust_reversible`, `assistant_stream_replies` (token streaming; off buffers the
turn whole, which is the escape hatch if a model's provider streams tool calls badly —
correctness does not depend on it either way, only time-to-first-word).
All of them are edited in Settings → Voice → **Mux assistant**; `assistant_stream_replies` was
the one that shipped without a control, so the escape hatch it exists to be was unreachable.
`create_project` additionally reads `new_project_parent` (Settings → Projects, not an assistant knob): shape-validated at save, existence-checked at use, and empty disables assistant project creation.

## Key files

- `src/swe_mux/assistant.py` — `AssistantService` (turn loop, tool bridge, trust policy, resolution, the duplicate guard and action ledger), `AssistantStore`, `_SentenceStreamer`, `restate_action`/`action_announcement`, the tool definitions, the primer.
- `src/swe_mux/openrouter.py` — `complete_tools`, the bounded tool-calling completion, and `_ToolStreamAccumulator` behind its optional SSE path;
  `marks_cache_breakpoints` / `cache_stable_message` (which message a caller marks is the caller's decision, since only it knows what is stable across calls), `apply_session_routing`, and `cached_prompt_tokens` / `cache_write_prompt_tokens` / `cache_discount_usd`, which read either shape a provider reports its cache figures in.
- `src/swe_mux/server.py` — assistant HTTP handlers and service wiring (note read/write closures, history search, spawn/interrupt/end operations shared with session control, and the Project Action catalog/preview/run closures over `_start_project_action`).
- `src/swe_mux/project_actions.py` — `preview_action_run`, the shared resolve-and-refuse used by the assistant preflight.
- `frontend/src/assistant.ts` — client dialog view, event reducer, follow-up window, spoken-verdict grammar, API calls.
- `frontend/src/assistantSpeech.ts` — one speech stream per turn: sentence appends, the card announcement joining the same stream, the close, and the queued-not-returned `spoke` flag behind the completion fallback.
- `frontend/src/AssistantPanel.tsx` — the conversation view and action cards, at all three `variant`s (`full`, `peek`, `hidden`), plus the open-card and reply signals the dock's chip reads.
- `frontend/src/voiceFuzzy.ts` — tier 2, the conservative fuzzy pass in front of the fallback.
- `frontend/src/earcons.ts` — the synthesized acknowledgment sounds.
- `frontend/src/App.tsx` — tier wiring in the voice catch-all, the UI-action executor, the single mount point, and the dock's size state (`voiceDock.ts`).
- `tests/test_assistant.py`, `frontend/test/assistantEvents.test.ts`, `frontend/test/voiceFuzzy.test.ts`.
