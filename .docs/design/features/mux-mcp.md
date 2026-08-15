# mux MCP: the agent return path (reads + two bounded writes)

## What it is

A streamable-HTTP MCP endpoint (`POST /mcp`) hosted in the daemon that gives every spawned
agent session visibility into the fleet — sibling sessions, their live status,
bounded transcript reads, indexed search over conversation history, exact Agent
Context sources, Project notes, and request outcomes, plus two bounded write tools added in Phase 5.
Every tool answers within the caller's own Project until the caller asks for more with the
shared `project` argument.
Roadmap Phase 4.5 (reads), Phase 5.6 (situational-awareness reads), and Phase 5
(`notify`, `request_spawn`) / control-plane build-order step 2.5
(`CONTROL_PLANE_ROADMAP.md` §7.1–7.5). Registered automatically into every spawned Claude
and Codex session — no user setup — and reachable by a user-typed `claude`/`codex` inside a
mux shell session via the agent shims.

**No tool delivers anything.** `notify` stages a message in another session's Phase 4
queue, where head-of-line order, receiver readiness, and (by default) human arming still
apply; `request_spawn` writes an inert Fleet Queue approval draft and starts nothing.

`run_action` is the one tool that starts a process, and its authority is borrowed rather than
granted: it can run only a command whose exact bytes a human already approved through the Project
Run menu, and an agent that edits a task file un-approves it. An agent therefore cannot approve
its own command. This grants strictly less than the caller already has, since an agent in a mux
session holds a shell; what it adds is that the command is one a human has seen. The v1
memory tools (`provenance`, `priorResolutions`, `deadEnds`) stay in control-plane step 8.

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
| `read_transcript` | a small indexed window around a `search_history` hit, or a bounded pageable head/tail of exactly one run; `self` is the default and `agent_run_id` unambiguously selects the caller's retired run |
| `search_history` | globally ranked compact hits across indexed Claude/Codex conversations, with role, title, backend, state, run, session-date, message-date, matching-mode, diversity, detail, output-budget, and cursor controls; each hit names the Project it came from |
| `memory_sources` | a descriptor-driven instruction and provider-memory inventory, including source attribution, capability, revision, modification time, and entrypoint kind; every source names its Project |
| `read_memory` | one bounded inventoried Agent Context source by opaque `source_id`, never by a caller-supplied filesystem path |
| `project_notes` | read-only inventory of Project notes, excluding the global Scratchpad; every note names its Project |
| `read_project_note` | one bounded Project note by opaque note id, with paths omitted and credential-shaped content withheld |
| `project_actions` | what a Project declares as runnable: native actions, imported VS Code tasks, and package scripts, each with its source file, steps, declared inputs, and whether a human has approved that file's exact current bytes; `include_schema` returns the `.swe-mux/actions.toml` authoring reference in the same result |
| `message_status` | current outcome of one `notify`, visible only to its attributed sending session, wherever it was sent |
| `spawn_requests` | status of spawn requests attributed to the caller; approval remains a human Fleet Queue act |
| `notify` | stages a message with a visible sender/message/correlation envelope, and a `from_project` header when it crossed a Project; also used to *reply* to a session that messaged you, which continues the same thread; returns the message id, correlation id, state, thread id, chain depth, and how many messages the thread has left |
| `request_spawn` | writes an inert spawn approval row into the Fleet Queue of the Project that would run it; returns the request id and starts nothing |
| `run_action` | starts one **already-approved** Project Action; each step becomes an ordinary terminal session and the result names the session ids. An unapproved action refuses with `trust_required` naming the file a human must review |

The write tools are listed even when disabled by config: they answer with a typed refusal,
because an MCP client caches `tools/list` at session start and a tool that vanishes is
indistinguishable from a broken server.

## Registration per backend

- **Claude**: one static `<data_dir>/claude-mcp.json` (`--mcp-config`, added by
  `ClaudeAdapter._args` and by the shim via `MUX_CLAUDE_MCP_CONFIG`): HTTP server entry with
  a literal URL and `Authorization: Bearer ${MUX_MCP_TOKEN}` env expansion — the token never
  lands in a shared file. Generated per-session settings allow the closed eleven-tool read set without a prompt and do not allow `notify`, `request_spawn`, or `run_action`; user deny/ask policy still has higher precedence. `--mcp-config` adds servers; user MCP config is untouched.
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

## Key files

- Protocol + tools: `src/swe_mux/mcp.py`
- The `project` argument, shared by the read and write surfaces: `src/swe_mux/project_scope.py`
- Closed read/write declarations and Claude read permissions: `src/swe_mux/mcp_contract.py`
- Write-tool policy (bounds, provenance, drafts): `src/swe_mux/agent_messaging.py`
- Endpoint handler, rate limit, wiring: `src/swe_mux/server.py`
- Token mint / env / meta mirror / adoption recovery: `src/swe_mux/session.py`
- Registration: `src/swe_mux/adapters/claude.py`, `src/swe_mux/adapters/codex.py`,
  `src/swe_mux/agent_launcher.py`, `src/swe_mux/launchers.py`
- Tests: `tests/test_mcp.py`, `tests/test_agent_messaging.py`, `tests/test_project_scope.py`

## Relates to

- `agent-messaging.md` — what the write tools may do, and every bound behind them.
- `prompt-queue.md` — where a `notify` lands and how it is delivered.
- `observations.md` - compatibility storage retained after the human Observation Inbox was retired.
- `delivery-readiness.md` — the operator-input evidence contract this phase also closed.
- `status-detection.md` — the status contract MCP reads through.
- `history.md` — the archive `search_history` queries.
- `../development/CONTROL_PLANE_ROADMAP.md` §7 - the full return-path design: §7.5 for the
  v0.5 situational-awareness reads (`ROADMAP.md` Phase 5.6) and the v1 memory tools
  (Phase 7.5), §7.6 for the planned session-control tools and their authority grant
  (Phase 7.6). Phase 5.6 is complete; later semantic-memory and control tools remain planned.
