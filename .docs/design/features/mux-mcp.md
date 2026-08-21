# mux MCP: the agent return path (reads + bounded writes and session control)

## What it is

A streamable-HTTP MCP endpoint (`POST /mcp`) hosted in the daemon that gives every spawned
agent session visibility into the fleet — sibling sessions, their live status,
bounded transcript reads, indexed search over conversation history, exact Agent
Context sources, Project notes, cross-session memory reads, and request outcomes, plus the
bounded write tools added in Phase 5 and the session-control tools added in Phase 7.6.
Every tool answers within the caller's own Project until the caller asks for more with the
shared `project` argument.
Roadmap Phase 4.5 (reads), Phase 5.6 (situational-awareness reads), Phase 5
(`notify`, `request_spawn`), Phase 7.5 (cross-session memory reads), Phase 7.6
(`interrupt`, `end_session`), and Phase 7.11 (`scan_timeline`, `scan_search`)
/ control-plane build-order steps 2.5, 8, and 9
(`CONTROL_PLANE_ROADMAP.md` §7.1–7.6). Registered automatically into every spawned Claude
and Codex session — no user setup — and reachable by a user-typed `claude`/`codex` inside a
mux shell session via the agent shims.

**No tool here delivers anything.** The one read that produces a message at all is
`watch_session`, and it produces exactly one, addressed to the caller itself, through the
ordinary queue where the same head-of-line order and readiness contract apply (see
"Session-settle watches" below). Neither `notify` nor `request_spawn` delivers either. `notify`
stages a message in another session's Phase 4 queue, where head-of-line order, receiver
readiness, and (by default) human arming still apply; `request_spawn` writes an inert Fleet
Queue approval draft and starts nothing. `notify(delivery="now")` asks for the item to be
delivered into a turn that is already running rather than at the target's next prompt; it
still delivers nothing itself, is refused unless the target's Project granted it and the
target session accepts it, and is authorized at delivery time by a strictly narrower
readiness predicate (`agent-messaging.md`, `delivery-readiness.md`). It does not stop the
turn - the CLI buffers the text and takes it at the turn boundary - so it is a latency
choice, and `interrupt` remains the way to stop one. The Phase 7.6 tools `interrupt` and `end_session`
are the first that act on a running agent, and they act only under a per-Project grant that
defaults to writing an inert approval a human must decide (see "Session control" below).

`run_action` is the one tool that starts a process, and its authority is borrowed rather than
granted: it can run only a command whose exact bytes a human already approved through the Project
Run menu, and an agent that edits a task file un-approves it. An agent therefore cannot approve
its own command. This grants strictly less than the caller already has, since an agent in a mux
session holds a shell; what it adds is that the command is one a human has seen. The v1
memory tools (`provenance`, `verifiedStatus`, `priorResolutions`, `deadEnds`) shipped in
Phase 7.5 and are covered in "Cross-session memory reads" below.

## Key concepts

- **MCP is transport, not authority (CP §7.1).** Every tool reads through the same services the browser routes use (`SessionManager`, `HistoryIndex`, `parse_transcript_cached`).
  The MCP layer owns only its agent-facing request validation, progressive result shape, redaction, and output budgets.
  Status read through it is bit-identical to what the UI shows because `SessionRecord.state` still has one writer, `apply_state_transition`.
- **Caller identity is injected, never claimed (CP §7.4).** A per-session bearer token is
  minted at spawn (`MUX_MCP_TOKEN`, beside `MUX_MCP_URL`), mirrored into the supervisor's
  session meta, and recovered at adoption — so it survives daemon restarts the same way the
  hook secret does, with no token table to invalidate. No tool has a sender parameter. An
  empty token (a session spawned before this feature) never authenticates.
- **Scope defaults to the caller's Project and widens only on request.** Every tool takes a
  `project` argument resolved by `src/swe_mux/project_scope.py`: omitted (or `"self"`) is the
  caller's `project_id`, falling back to the git `project_scope_id` for sessions in no
  registered Project; `"fleet"` is every Project; a Project name or id is that one.
  Nothing widens implicitly, and the fallback scope of an unregistered caller never widens at
  all. A target outside the requested scope answers exactly like a target that never existed —
  confirming existence is itself a leak. History queries go through `search_history_index`,
  which keeps the `agent_visible=1` quarantine predicate.
- **A default an agent cannot discover reads as a prohibition.** The widening is stated in
  three places, because an agent reads answers far more often than it reads a tool schema:
  the tool description, the refusal (`no such session in your Project. Pass
  project:"fleet"…`), and the result envelope. Every result carries `project_scope`, and a
  default `list_sessions` also reports `live_sessions_in_other_projects` with a `scope_note`
  naming the argument that would include them. An unknown Project name is refused with the
  names that do exist rather than with an empty result, which would read as "that Project
  holds nothing".
- **A widened name may match twice.** Two Projects may each hold a session called `backend`.
  Resolution never picks one: it answers `ambiguous_target` (writes) or `AmbiguousIdentity`
  (reads) listing the candidate session ids. `notify` also accepts a qualified
  `"Project name/session name"` target, tried only after the plain form so a session whose own
  name contains a separator still resolves as itself.
- **A search hit is not a capability.** `hit_id` embeds the Project of the row that produced
  it, not the caller's. Reading it back through `read_transcript` needs a scope that admits
  that Project, so a fleet search followed by a default read refuses rather than crossing
  silently.
- **Project-singular reads take a name, not a widening.** `read_memory` and
  `read_project_note` resolve one opaque id that belongs to one Project. `project:"fleet"`
  makes them walk Projects until one owns the id, which is what makes a fleet-wide
  `memory_sources`/`project_notes` listing actionable; a fleet inventory covers at most
  `FLEET_INVENTORY_MAX_PROJECTS` (25) Projects, caller's own first, and says
  `projects_truncated` when it stopped.
- **Return nothing over a weak match (CP §7).** Empty results are fine;
  plausible-but-wrong teaches an agent to stop calling.
- **Bounded and redacted.** Transcript, Project-note, and Agent Context source reads are capped at 512 KiB.
  Ordinary transcript reads default to 12 messages and 32 KiB of message text, remain explicitly expandable to 200 messages and 512 KiB, and are pageable in either direction through run-bound opaque cursors.
  `list_sessions` returns at most 25 compact entries and 32 KiB across live and ended rows combined, with query filtering and an opaque continuation cursor.
  History search defaults to eight compact hits and a 16 KiB hit-payload budget, with explicit bounds for larger calls.
  Detailed session records go out only when `detail=full` and still use the `session_summary` allowlist, never `record.snapshot()`, which carries `spawn_env`.
  Any message or excerpt that trips the clipboard credential gate (`looks_like_secret`) is replaced with a redaction marker.
- **History retrieval is progressive.** `search_history` filters and ranks indexed messages server-side, returning only a title, role, timestamp, bounded excerpt, and opaque `hit_id` by default.
  During a post-upgrade FTS repair it uses bounded literal database filtering and returns `search_index_ready=false`; this preserves complete retrieval while explicitly withholding ranked-index readiness.
  The caller passes that `hit_id` to `read_transcript`, which returns one message before and two after the match by default instead of loading an entire transcript.
  The hit embeds the Project scope, run, message ordinal, and transcript-index watermark.
  A changed index makes the hit stale and requires a new search, so a remembered pointer cannot silently select different text.
  Agents may widen either stage explicitly with result count, byte budget, detail, and before/after controls.
- **Session names match the UI.** Session summaries preserve the backend-generated `name`
  and also expose `display_name`, computed with the browser's rule: the latest title annotation
  wins only while the session is auto-named. Exact, unique display names resolve anywhere a
  session target is accepted; duplicate display names resolve nothing rather than selecting a
  plausible-but-wrong session. Stable session IDs remain the unambiguous identity.
- **A session's identity includes which conversation it is on.** `agent_run_seq` counts the
  in-CLI `/clear`/`/new` replacements a session has been through (`backends.md`), so a caller
  holding a remembered `agent_run_id` can distinguish "a different session" from "the same
  session, a conversation later" — and the second means the agent it is reasoning about
  retains nothing it was previously told. `read_transcript` follows the live run and never
  splices two conversations into one read.
- **Past self is explicit.** `self` and an omitted `session_id` resolve to the caller, while `read_transcript(agent_run_id=...)` selects an exact current run or one of the caller's retired runs after an in-place conversation rollover.
  An explicit retired-run selector is resolved before live session ids, so a first run whose id equals the logical session id cannot collide with its successor.
  Every returned message carries that run id and its persisted `agent_run_seq`, and the result says `own_superseded_run: true` rather than presenting it as current memory.
- **Cross-Project access is explicit, not absent (changed 2026-08-14).** Phase 5.6 kept every
  read own-Project only, on the reasoning that a token's default scope is its Project
  (`CONTROL_PLANE_ROADMAP.md` §7.4). The default did not change; the prohibition did. Sessions
  in separate Projects legitimately need to hand work to each other, and the same-host
  boundary decision already establishes that this token is identity and read scope rather than
  an authorization boundary — so a caller that asks for another Project is not crossing a
  security line, and refusing it only removes a capability the caller could reach by other
  means. What the widening does cost is stated plainly: a fleet `search_history` ranks
  conversations from unrelated repositories against each other, which is why the argument is
  per-call and never a mode.
- **Same-host callers are fully trusted (decided 2026-07-28, re-affirmed for Phase 5 on
  2026-07-29).** The token is identity and read scope, not an authorization boundary. The
  Phase 5 re-examination concluded that a token check on the mutating routes cannot deliver
  the property it appears to: any same-user process on this host can request whatever
  credential the browser is given. The compensating design is that these tools grant
  strictly *less* authority than the un-tokened HTTP surface already does — no delivery, no
  spawn, no PTY write — so a compromised agent gains nothing here. Full reasoning and its
  limits: `agent-messaging.md` (§ same-host boundary).
- **A refused write is a result, not a protocol error.** Bounds (`body_too_large`,
  `origin_budget_exhausted`, `relay_cycle`, …) come back as typed `isError` content so the
  agent can adapt or stop, rather than as a JSON-RPC fault it will retry blindly.

## Tool surface

Every tool takes `project` (omitted = the caller's own, `"fleet"` = every Project, or a
Project name or id). `request_spawn` is the one exception: a request starts one session in one
Project, so it accepts a name but refuses `"fleet"` with `invalid_project`.

| Tool | Returns |
|---|---|
| `list_sessions` | a compact, queryable, pageable list of live and optionally recent ended sessions; the 25-row and 32 KiB caps apply to the combined result; ordered caller first, then own Project, then anything the widening added, so a fleet call never pushes a sibling off the first page |
| `get_session` | status + metadata and a run brief by id, exact name, or `self`; the caller's own row also lists superseded run ids; `self` and the caller's own runs resolve whatever `project` asked for. Every result carries `completion_mode` and `exit_code`, and `output_bytes` returns a bounded redacted tail of a shell or task session's terminal output |
| `read_transcript` | a small indexed window around a `search_history` hit, or a bounded pageable head/tail of exactly one run; `self` is the default and `agent_run_id` unambiguously selects the caller's retired run; turns the conversation branched away from are excluded and counted in `abandoned_messages` (`transcript-branches.md`) |
| `search_history` | globally ranked compact hits across indexed Claude/Codex conversations, with role, title, backend, state, run, session-date, message-date, matching-mode, diversity, detail, output-budget, and cursor controls; each hit names the Project it came from |
| `memory_sources` | a descriptor-driven instruction and provider-memory inventory, including source attribution, capability, revision, modification time, and entrypoint kind; every source names its Project |
| `read_memory` | one bounded inventoried Agent Context source by opaque `source_id`, never by a caller-supplied filesystem path |
| `project_notes` | read-only inventory of Project notes, excluding the global Scratchpad; every note names its Project |
| `read_project_note` | one bounded Project note by opaque note id, with paths omitted and credential-shaped content withheld |
| `project_actions` | what a Project declares as runnable: native actions, imported VS Code tasks, and package scripts, each with its source file, steps, declared inputs, and whether a human has approved that file's exact current bytes; `include_schema` returns the `.swe-mux/actions.toml` authoring reference in the same result |
| `message_status` | current outcome of one `notify`, visible only to its attributed sending session, wherever it was sent; an `armed` result also carries `target_delivery`, because "armed" alone cannot distinguish a peer that is busy from one nothing can reach without a human |
| `spawn_requests` | status of spawn requests attributed to the caller; approval remains a human Fleet Queue act |
| `provenance` | cross-session lineage for one file: who wrote which content hash, who later read it, and the tests those runs ran, from Tier 0 facts plus `build_provenance_edges`. Ambiguous edges (another write landed between the reported write and read) are withheld and only counted. Lineage, never blame |
| `verified_status` | whether a claim is tested or only declared done, via `detect_declared_vs_verified` over a run's Tier 0 test facts; reports "claims done · tests ran · tests passed", "tests failed", or "nothing verified". Defaults to the caller's own current run; `session_id` targets another |
| `prior_resolutions` | a previously verified fix for an exact normalized error signature, from the experience corpus (`automation_store.experiences`), matched on equality of the error fingerprint and never a substring. Low-confidence (<0.5) matches are withheld and only counted |
| `dead_ends` | approaches abandoned or failed within a run with a recorded dead-end note, from scan records; `subsystem` matches as a substring of the record's target paths, intent, or summary. Low-confidence (<0.4) records are withheld. A conversation rollover writes a boundary not a record, so `/clear` never counts as an abandonment |
| `doc_debt` | which docs owe an update for the Project's recently changed source files, as `{doc, changed_files}` pairs re-derived from each doc's "Key files" section (`build_doc_debt_map` over `build_doc_ownership`, inverted to `doc -> changed files` over a 24h Project fact window, a doc edited in that window excluded). Not scraped from the doc-debt annotation, whose content is a human sentence. Blind spot named in the description: a source file no doc lists produces no debt, so empty is not proof the docs are current (Phase 7.10) |
| `scan_timeline` | one session's distilled behavioral spine instead of its raw conversation. `detail:"digest"` (the default) is the bounded `catch_me_up` rollup - phases, claims, current blocker; `detail:"records"` is the compact per-window projection, newest first, cursored by `since_t1`; `detail:"full"` expands at most five explicitly named `record_ids`. Filters: time window, `blocked_only`, `work_phase`, `approach_status`, a `target` path fragment, `exclude_heartbeat`. Every result carries the enablement/liveness block. Reads an ended session too. Gated on `scan_reads` (Phase 7.11) |
| `scan_search` | runs found by what they were *doing*: a query resolved against distilled scan `summary`/`intent`/`target` records rather than raw transcript text, all terms required. Each hit names its `agent_run_id` and its `t0`/`t1` window. The agent-facing exposure of the shipped `GET /api/history/scan-search`. Gated on `semantic_history_search` (Phase 7.11) |
| `blast_radius` | everything a change to one file can reach: reverse callers (hop-ordered), the git co-change net, covering tests among the reachable set, and owning docs, from the Phase 7.9 code-structure graph. The static reverse set is a lower bound and says so; named blind spots are `getattr`, dict dispatch, decorators, DI, dynamic imports. Gated on `code_graph` |
| `find_definition` | where a symbol is defined, by leaf name or qualname, from the code graph. Gated on `code_graph` |
| `find_callers` | the (file, symbol) pairs that call into a file or symbol, resolved import-aware so a same-named symbol in an unrelated module is not a false caller; unresolved same-name callers are reported separately. A lower bound. Gated on `code_graph` |
| `find_references` | every call or reference to a symbol in a file — the precise structural neighborhood, not a grep. Gated on `code_graph` |
| `code_context` | a compact structural neighborhood for context packing: each file's key symbols, imports, and direct callers, instead of reading whole files. Gated on `code_graph` |
| `test_gap` | recently-changed files whose static blast radius contains no covering test. A lower bound: a test reaching the code through dynamic dispatch is invisible, so a listed file is a candidate not a proof. Gated on `code_graph` |
| `watch_session` | arms a one-shot watch on another session and returns having delivered nothing. Exactly one deterministic notice then enters the **caller's own** prompt queue: when the target leaves working for a settled state and holds it, when it ends, or when the caller's timeout elapses - whichever is first. The notice names which case fired and the target's state, including the `awaiting` sub-reason and any background work still running. Bounded per watcher, one per target, ephemeral (see "Session-settle watches" below) |
| `notify` | stages a message with a visible sender/message/correlation envelope, a `from_project` header when it crossed a Project, and a `delivery` header when it landed mid-turn; also used to *reply* to a session that messaged you, which continues the same thread; returns the message id, correlation id, state, thread id, chain depth, how many messages the thread has left, and `target_delivery` — whether anything will actually deliver it, and what is stopping it if not |
| `request_spawn` | writes an inert spawn approval row into the Fleet Queue of the Project that would run it; returns the request id and starts nothing |
| `run_action` | starts one **already-approved** Project Action; each step becomes an ordinary terminal session and the result names the session ids. An unapproved action refuses with `trust_required` naming the file a human must review |
| `interrupt` | stops the target agent's current turn (writes the interrupt byte through the shared operator-input path); the session, conversation, and PTY survive. Refused unless delivery-readiness is `safe`, and it cannot target the caller's own session. Under the default `draft` grant it writes an inert approval instead of acting |
| `end_session` | ends the target session (`self` allowed); tries the harness's own graceful exit sequence, then a hard-stop fallback. A self-end returns before teardown and leaves the record readable. Under the default `draft` grant it writes an inert approval instead of acting |
| `request_land` | enqueues a land of the caller's **own** worktree branch onto its Project's trunk; performs nothing itself. The daemon then reconciles, verifies, and fast-forwards, one branch at a time, and hands a conflict or a failed gate back as a queue message. Gated on the `land_queue` automation, and under the default `draft` grant it writes an inert approval instead of enqueueing (`land-queue.md`) |

`request_land` deliberately takes no target. The checkout comes from the caller's own live
cwd, so "an agent lands the checkout it is working in, and no other" holds by construction
rather than by a check that could be routed around. There is nothing in the call to forge.

The write tools are listed even when disabled by config: they answer with a typed refusal,
because an MCP client caches `tools/list` at session start and a tool that vanishes is
indistinguishable from a broken server.

## Cross-session memory reads (Phase 7.5)

The four memory reads make swe-mux's third-person, all-sessions record queryable by a
first-person agent mid-task. They are deterministic queries over shipped substrate - Tier 0
facts, the git-provenance edges, the experience corpus, and the scan timeline - and add no
authority. The v0.5 memory-source reads (`memory_sources`, `read_memory`) are separate and
shipped in Phase 5.6.

- **Precision over recall.** Each tool prefers an empty result to a weak match, because an
  agent that acts on one plausible-but-wrong answer either stops calling or propagates the
  error. `prior_resolutions` matches on equality of the normalized error fingerprint and never
  a substring; `provenance` withholds an ambiguous edge; low-confidence experiences (<0.5) and
  dead-ends (<0.4) are withheld and only counted for the human.
- **Every result names the run it came from.** A `run` / `writer` / `reader` / `source_run`
  object carries `run_relation`, one of `your_current_run`, `your_earlier_run` (the caller's
  own run superseded by an in-CLI `/clear`), `sibling_run`, or `unknown`. A result from the
  caller's own retired run is labelled rather than blended into the present, because after a
  `/clear` the agent has no memory of its predecessor's work and an unlabelled return would read
  as its own recollection (`backends.md`, Phase 5.4).
- **Per-Project opt-in through the enablement DAG.** Each tool gates on a specific automation
  (`MEMORY_TOOL_AUTOMATION` in `mcp.py`): `provenance` → `provenance_graph`,
  `verified_status` → `declared_vs_verified`, `dead_ends` → `dead_end_memory`,
  `prior_resolutions` → its own `prior_resolutions` automation, and `doc_debt` → the
  `doc_debt` detector's automation (all in `automation_registry.py`, each requiring `tier0`).
  `doc_debt` (Phase 7.10) reads the same doc-ownership substrate the `doc-debt` detector
  writes, so it reuses that detector's opt-in rather than a new consumer id.
- **Off is never a fake empty.** When the daemon does not run the memory substrate the tool
  raises `unsupported` (503); when no Project in scope has opted the backing automation in it
  raises `disabled` (409) naming the automation. An agent that cannot tell "off" from "nothing
  here" trusts a silence it should not.

## Scan-timeline reads (Phase 7.11)

The scan timeline is the most compressed artifact swe-mux produces about a conversation, and
until Phase 7.11 it was the only one agents could not read.
An agent asked to review or monitor a sibling had to page `read_transcript` over raw
conversation while a distilled, semantically-labeled index of that same conversation already
existed, paid for and stored.
`scan_timeline` and `scan_search` mirror the shipped `search_history` → `read_transcript`
pair one level up: search broadly across runs, then read one run's spine deeply.

- **Projection before endpoint.** A stored record averages ~3.2 KB and a long run holds
  hundreds, so exposure is a projection problem first.
  The compact projection drops `evidence_refs`, `tier0_fact_ids`, `prompt_hash`,
  `prompt_version` and `observer_model` (42% of stored bytes, none of it actionable) and
  collapses `target` to a count plus at most three paths (another 17%, and the single largest
  field in a record).
  Field selection alone is not enough: the page is bounded to 30 records by default and 100 at
  most, because even the projection is ~730 bytes a row.
- **Three fields survive that look like metadata and are not.**
  `repaired_fields` says which fields were coerced rather than asserted - `_ENUM_FALLBACKS`
  substitutes `unknown`/`none` for an out-of-range enum, and a stored fallback is otherwise
  indistinguishable from a model assertion.
  It is a per-field classification rather than the raw `repairs` list because most repairs are
  a cosmetic `behavior` dedup and an unclassified list cries wolf.
  `messages_seen` and `window_truncated` say how thin the window behind a judgement was: a
  `work_phase` decided from one `tool_result` and one decided from forty messages are not the
  same claim.
- **An absent field is not an uncertain one.** A record that carries no `approach_status`
  omits the key entirely rather than rendering as `unknown`, so the model's silence and the
  model's uncertainty never read the same.
- **The cursor is what makes monitoring affordable.** `since_t1` is exclusive, so feeding back
  the newest `t1` already seen returns strictly newer records and never repeats the boundary
  one. It, and every other filter, runs in SQL (`json_extract` for the semantic ones), so a
  bounded page means "rows returned" rather than "rows scanned" - a filtered page that ran in
  Python would come back short and a caller could not tell that from the end of the run.
- **Off is never a quiet session.** Every result carries `scan_state`: `scanning`,
  `last_scan_at`, `skip_reason`, `run_decided`, `run_enabled`, `project_enabled` and the
  closest-to-binding gate. A scanner stopped by a budget cap and a session that is simply
  quiet both return an empty tail, and only one of them is worth acting on. `liveness()` in
  `scan_timeline.py` is the single owner of that block, shared with the drawer's snapshot, so
  the two surfaces cannot disagree.
- **Ended sessions are readable.** Records outlive their session and "what did that finished
  sibling do" is the read the tool exists for, so an ended session resolves through history and
  reports `session_live: false`. Its Project-context-derived fields report unknown rather than
  `false`, because a context that cannot be resolved is not an opt-in that is off.
- **`scan_timeline` gates on the target session's Project, not the caller's.** It is
  session-scoped, so the question is "did *that* Project opt in", which is a different question
  from the Project-wide memory reads' "which of the Projects I may see opted in".
- **`scan_reads` is its own consumer id, not the `scan_timeline` substrate.** A distilled intent
  summary is in some ways more revealing than the transcript excerpt behind it, so a Project
  must be able to keep its timeline and still withhold it from sibling agents. `scan_search`
  instead inherits `semantic_history_search`, the opt-in that already gates the identical query
  on the human surface.
- **No write surface, deliberately.** Neither `POST /api/sessions/{sid}/scan-timeline/scan`
  nor the backfill endpoint is reachable through MCP. Reads cost nothing, but a scan spends
  the human's gated budget against caps set in Settings → Automation → Scan timeline, and an
  agent that could trigger scans could exhaust `scan_timeline_daily_budget` for every
  Project on the host. Source rehydration stays behind
  `GET /api/sessions/{sid}/scan-timeline/{record_id}?rehydrate=1` for the same reason at
  smaller scale: it reparses a transcript, and that cost does not belong behind a list read.
  There is no generic record-dump tool either; every tool stays a question, not a table.
- **Composition is named in the descriptions, because an agent reads answers more often than
  schemas.** A scan record's `t0`/`t1`/`agent_run_id` reach the raw messages through
  `search_history(run_ids, message_after, message_before)` and then `read_transcript(hit_id)`.
  `read_transcript` has no time-window argument of its own, so an agent told only "use the
  window" would try something that does not exist.

## Session-settle watches

An orchestrator running many workers had no way to be *told* when one of them
stopped working.
It polled `list_sessions` (or `/api/sessions` from an ad-hoc script), spending a turn per poll, and the only observable it got back was `idle` - which also means "stalled at a question nobody answered".
`watch_session` moves that loop into the daemon, where the state already lives and costs nothing to read.

- **A watch is a read that matures into exactly one bounded message.**
  It grants no authority the caller did not already have.
  The target is only ever read - the same `SessionRecord.state` `get_session` returns - and the single write it produces is a fixed daemon-authored template into the **caller's own** prompt queue, staged as a `rule` sender: the deterministic-observer path the land queue's handback already uses (`prompt-queue.md`, `land-queue.md`).
  Nothing here writes a PTY, addresses a third session, spends a budget, or triggers a scan.
  That is why it is declared a read tool: it addresses nobody, actuates nothing, and re-arming returns the watch that already exists, so it grants strictly less than the `list_sessions` polling loop it replaces - and permission-gating it would put an approval prompt in front of the monitoring call an orchestrator makes most often, for nothing.
- **The timeout is the point, not the fallback.**
  Either the watch resolves or it says it did not, and both notices name the case and the target's state at that moment.
  A hung worker can therefore never be confused with a watch that quietly evaporated, which is the failure this was asked for.
  The timeout is checked *after* the settle rules on the same sweep, so a settle that matured on that pass reports the case that actually happened; a timeout that lands mid-hold reports the timeout and the state it saw, because a timeout that waited for the hold would be a suggestion rather than a bound.
- **A settle is an edge, and it has to hold.**
  Firing on the first `idle` would be wrong about two times in five: on a measured 10-hour, 17-session day, **89 of 211 idle transitions were back to `working` inside 120 s** with no human input in between (`notifications.md`).
  The watch therefore requires the target to have been observed **working** and then to hold a settled state for the same 120 s that surface holds a "ready" alert for; a flap restarts the hold rather than accumulating across it.
  `starting` deliberately does not count as working: a booting session reaches `idle` through `startup_quiet_fallback`, which is inferred from PTY quiet and is not even input-ready, so counting it would fire "your worker finished" before the seed prompt had run a turn.
- **`awaiting` is settled, and the notice never calls it done.**
  Being blocked on a person is exactly the outcome an orchestrator most needs told apart from a finish, so it resolves the watch and the notice states the state and its sub-reason rather than a verdict.
- **An idle session with running background work has not finished.**
  The turn ended; the agent did not, and it will resume itself when its subagents or background tasks land.
  The watch suppresses that case the same way the notification path does and for the same reason (`RUNNING_ACTIVITY_KINDS`, `idle_reason: waiting_on_background`) - and the timeout is what stops the suppression from becoming silence.
- **Ended fires unconditionally.**
  A session that exited or crashed will never work again, so requiring a working edge there would guarantee a timeout.
  A target that has *already* ended is refused at arming time with its final state, rather than answered by a notice half a second later.
- **Ephemeral, and never silently so.**
  Watches live in daemon memory: they die with the watcher session, are dropped when the watcher's conversation rolls over (an in-CLI `/clear` mints a successor that never armed the watch and would read the notice as a recollection it does not have), and do not survive a restart.
  Because a daemon restart under live sessions is a routine act here, stopping the service **flushes every open watch as a notice** before the prompt queue stops, so a restart says "your watch was dropped, re-arm it" instead of leaving a promise nothing could keep.
- **Bounded and one-shot.**
  A few watches per watcher (`session_watch_max_per_session`, default 8), one per target - re-arming returns the existing watch rather than a second copy of one notice - a ceiling on the timeout (`session_watch_max_minutes`, default 240), and an install-wide kill switch (`session_watch_enabled`).
  A `0` timeout is refused rather than read as "omitted": it means "no timeout", which is the one shape this service will not promise.
- **The result says what will deliver the notice, because "queued" alone is unactionable.**
  A `rule` sender is never self-arming (`prompt-queue.md`), so the notice waits in the watcher's queue for the operator exactly like a land-queue handback, and the arming result says so rather than letting a caller wait for a message no machine was going to hand it.
  This is the same lesson `notify`'s `target_delivery` records.

What is deliberately absent: no per-Project opt-in and no grant, because a watch reads what the caller can already read and writes only to itself; and no self-arming exception for a self-requested `rule` item, which would be a change to the queue's arming rule and belongs to that contract rather than to this feature.

## Session control (Phase 7.6)

`interrupt` and `end_session` are the first MCP tools that act on another running agent.
MCP is transport, not authority: every bound lives in `SessionControlService`
(`session_control.py`), and the tool is a thin caller. Both operations - the interrupt and the
graceful end - are shared daemon operations the browser and CLI call too
(`interfaces.md`), so an agent-initiated stop takes the identical accounted path as an
operator's.

- **`interrupt(target)`** stops the target's current turn and the session lives on. It is a
  PTY write, so it is refused unless delivery-readiness is `safe`: `blocked` refuses and
  `unknown` never authorizes (fail closed), because interrupting a session mid-approval-prompt
  or in a menu is corruption, not a stop. It cannot target the caller's own session.
- **`end_session(target)`** ends the session, and `self` is allowed and is the ordinary
  finished-worker case. A self-end returns its result **before** teardown begins; the final
  turn is flushed and the record stays readable through `list_sessions(include_ended)`,
  `get_session`, and history. An agent may end itself; it may not erase itself.
- **A three-position per-Project grant.** `off` is the absence of the `session_control`
  automation opt-in and refuses both tools. `draft`, the default once opted in, makes the call
  write an inert `control_request` observation that a human approves in the Fleet Queue - the
  approval is what acts. `granted` acts directly, inside bounds. The draft/granted split is the
  `.swe-mux/config.toml` field `session_control_grant` (`"draft"` | `"granted"`, default
  `"draft"`), read by `project_session_control_grant()`.
- **Bounds on the granted path.** A per-origin hourly budget (charged only when the granted
  path acts, not for a draft), a reciprocal-cycle guard (A interrupting B while B recently
  controlled A is refused `relay_cycle`), idempotency by `correlation_id`, and typed refusals
  rather than JSON-RPC faults. The install master switch is `config.session_control_enabled`
  (default true), with `session_control_hourly_budget` (default 30) and
  `session_control_graceful_timeout_s` (default 12.0).
- **Granted spawn.** `mux.requestSpawn` takes the same three-position model. A per-Project
  `spawn_grant` (`"draft"` | `"granted"`, default `"draft"`, gated by the same `session_control`
  automation, read by `project_spawn_grant()`) decides whether the call creates a session in the
  target Project directly or writes the Phase 5 inert draft. Authority is by target Project, so
  an agent can spawn into any registered Project that granted it (and, since interrupt/end are
  also target-authority, monitor and end that session too). The granted spawn goes through the
  same `_spawn_from_body` path the browser and Fleet-Queue approval use, is capped by a
  dedicated per-origin `agent_spawn_hourly_budget` (default 10), and emits an
  `agent_session_control` event with `action:"spawn"`. The default everywhere stays the inert
  draft, so nothing spawns directly until an operator raises a Project's grant.
- **What stays impossible at any grant.** A target outside the requested scope is
  indistinguishable from nonexistent; a shell or other non-agent pane is refused; and the
  session that hosts the running daemon is refused (`_session_owns_daemon`, a psutil ancestry
  check), because job-object inheritance means ending it would take the daemon down. There is
  no automatic remediation: "interrupt and re-run the turn" is resampling, which amplifies
  injected content, so a rewind stays human-directed.
- **Never silent.** Every action emits an `agent_session_control` event with the calling
  session and run as provenance; a draft also emits `agent_control_drafted`. Drafts surface in
  the Fleet Queue beside spawn requests as `control_requests`, approved through the existing
  `POST /api/projects/{project_id}/observations/{observation_id}/decide` route.
- **Durable end reasons.** An agent-initiated end (graceful or hard fallback) records
  `agent_ended`, distinct from an operator `killed` and a CLI-initiated `exited`/`completed`
  (`sessions.md`, `data-model.md`).

## Registration per backend

- **Claude**: one static `<data_dir>/claude-mcp.json` (`--mcp-config`, added by
  `ClaudeAdapter._args` and by the shim via `MUX_CLAUDE_MCP_CONFIG`): HTTP server entry with
  a literal URL and `Authorization: Bearer ${MUX_MCP_TOKEN}` env expansion — the token never
  lands in a shared file. Generated per-session settings allow the closed sixteen-tool read set without a prompt and do not allow `notify`, `request_spawn`, `run_action`, `interrupt`, or `end_session`; user deny/ask policy still has higher precedence. `--mcp-config` adds servers; user MCP config is untouched.
- **Codex** (>= 0.145): argv overrides `-c mcp_servers.mux.url="…"` and
  `-c mcp_servers.mux.bearer_token_env_var="MUX_MCP_TOKEN"` — natively env-based bearer, no
  stdio shim needed. Shim path mirrors it for user-typed `codex`.
- Both shim paths register only when `MUX_MCP_TOKEN` is present in the environment; a
  registration without a token would only produce 401s inside the CLI.

## Restart tolerance

The daemon restarts under live sessions by design. An in-flight call dies with the TCP
connection — a transport error every MCP client treats as retryable; the server never
returns a partial or fabricated result (a transcript parse that misses its 2 s budget
reports itself as transient and retryable). After the restart the same token works again:
adoption recovers it from supervisor meta. The listen port is stable, so registered CLI
configuration is never rewritten. An unknown token gets a typed 401 explaining that the
session ended or predates the surface — explicitly *not* a retry-forever condition.

## Operations

- Loopback-only, like hook ingress; the Host allowlist of `security_middleware` applies.
- Per-session sliding-window rate limit (120 calls/min) with the same sweep pattern as
  `hook_ingress_windows`; 256 KiB request-body cap.
- `calls`/`denied`/`writes` counters and per-tool call/response-byte/truncation totals appear under `mcp` in `GET /api/diagnostics/background`.
- Read-tool logs contain tool, caller, and Project metadata only.
  Source contents, prompt text, and SSH credential text are never logged.
- JSON-RPC methods: `initialize` (protocol 2025-06-18, older versions negotiated),
  `ping`, `tools/list`, `tools/call`; notifications get 202. Batching is rejected.
- Every tool advertises MCP annotations from the shared closed contract: reads are read-only and idempotent, while both writes remain permission-gated.

## Live testing

The stub tests in `tests/test_mcp*.py` prove the tool logic against fakes.
Two gated live tiers prove the surface against real agents and the real endpoint.
The in-process automations tier (`tests/test_live_automations.py`, `SWEMUX_RUN_LIVE_AUTOMATIONS_TESTS=1`) runs a real CLI, captures the Tier 0 facts that run produced, and asserts the memory reads (`provenance`, `verified_status`) over those real facts with no daemon and no port.
It also proves `dead_ends` and `prior_resolutions` against a real store round-trip, because neither has a deterministic offline producer.
The wire tier (`tests/test_live_mcp_control.py`, `SWEMUX_RUN_LIVE_MCP_TESTS=1`) stands up an isolated daemon on an ephemeral port, spawns a real agent, recovers its bearer token from the live process environment with psutil, and drives `/mcp` end to end for `get_session`, `notify`, `interrupt`, `end_session`, and granted `request_spawn`.
The same read of the child environment asserts the per-session env override held, so the spawned agent's `MUX_HOOK_URL` names the isolated daemon rather than the live fleet.
Both tiers are excluded from `.worktree-verify` and CI.

## Key files

- Protocol + tools: `src/swe_mux/mcp.py`
- Scan-record projection, the bounded digest, and the repair classifier:
  `src/swe_mux/scan_consumers.py`
- The enablement/liveness block both this surface and the drawer read:
  `ScanTimelineService.liveness` in `src/swe_mux/scan_timeline.py`
- Scan-record predicates and the `since_t1` cursor, in SQL:
  `AutomationStore.scan_records` in `src/swe_mux/automation_store.py`
- The `project` argument, shared by the read and write surfaces: `src/swe_mux/project_scope.py`
- Closed read/write declarations and Claude read permissions: `src/swe_mux/mcp_contract.py`
- Write-tool policy (bounds, provenance, drafts): `src/swe_mux/agent_messaging.py`
- Session-control authority and bounds (grant, budget, cycle, idempotency, readiness gate):
  `src/swe_mux/session_control.py`
- Settle-watch bounds, the fire rules, and the notice template:
  `src/swe_mux/session_watch.py`
- Shared interrupt/graceful-end daemon operations, the daemon-owner guard, and the drafted
  control-request approval: `src/swe_mux/server.py`
- Endpoint handler, rate limit, wiring: `src/swe_mux/server.py`
- Token mint / env / meta mirror / adoption recovery: `src/swe_mux/session.py`
- Registration: `src/swe_mux/adapters/claude.py`, `src/swe_mux/adapters/codex.py`,
  `src/swe_mux/agent_launcher.py`, `src/swe_mux/launchers.py`
- Tests: `tests/test_mcp.py`, `tests/test_mcp_scan_timeline.py`,
  `tests/test_agent_messaging.py`, `tests/test_project_scope.py`,
  `tests/test_session_watch.py`;
  live tiers `tests/test_live_automations.py`, `tests/test_live_mcp_control.py`, with the
  in-process fact and isolated-daemon harnesses in `tests/support/live_facts.py` and
  `tests/support/live_daemon.py`

## Relates to

- `agent-messaging.md` — what the write tools may do, and every bound behind them.
- `prompt-queue.md` — where a `notify` lands and how it is delivered.
- `observations.md` - compatibility storage retained after the human Observation Inbox was retired.
- `delivery-readiness.md` — the operator-input evidence contract this phase also closed, and
  the fail-closed readiness gate the `interrupt` tool consumes.
- `automation-enablement.md` - the per-Project opt-in DAG the memory reads and session control gate on.
- `sessions.md` - the graceful session-end operation and the `agent_ended` reason.
- `observations.md` - the drafted `control_request` storage, mirroring `spawn_request`.
- `status-detection.md` — the status contract MCP reads through.
- `history.md` — the archive `search_history` queries.
- `scan-timeline.md` - the substrate `scan_timeline` and `scan_search` read, the record
  contract, and why no scan trigger is exposed here.
- `../development/CONTROL_PLANE_ROADMAP.md` §7 - the full return-path design: §7.5 for the
  v0.5 situational-awareness reads (`ROADMAP.md` Phase 5.6) and the v1 memory tools
  (Phase 7.5), §7.6 for the session-control tools and their authority grant (Phase 7.6).
  Phases 5.6, 7.5, and 7.6 are complete.
