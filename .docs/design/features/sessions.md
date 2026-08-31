# Sessions and terminals

## What it is

Daemon-owned interactive ConPTY processes with immutable Project ownership, bounded replay,
and reattachable browser viewports.

## Model

- A session is one adapter, one ConPTY process, bounded byte scrollback, and zero or more
  browser subscribers.
- `spawn_backend` and `spawn_native_session_id` are immutable root-process facts.
  `backend` and `native_session_id` may change only for a legitimate agent run promoted
  inside a root shell. A provider launched as the root can never be demoted by a child CLI.
- A session has one immutable canonical `project_id`. It cannot exist before a Project does.
- The daemon starts every new, split, stacked, resumed, or review session at the owning Project's canonical root unless the request names a contained subdirectory or exact Git-listed worktree root of the same repository.
- Validated OSC 7/runtime cwd is display and Git telemetry only. Navigating elsewhere does
  not change Project membership, layout, note, file browser, defaults, or history ownership.
- Runtime terminal boundary is explicit: `local`, `remote`, or `unknown`.
  A non-local OSC 7 authority clears local cwd and Git telemetry and records only the remote
  authority and transport state.
  A later validated local OSC 7 directory restores local integration availability.
  Agent promotion, transcript following, hook cwd, shim assumptions, and inferred delivery
  targeting are unavailable across a remote boundary; manual PTY input remains available.

## Operations

- Direct shell creation uses the requested/profile/Project/global profile precedence.
- Project Actions create ordinary shell-backed sessions. Every step is attributed to the
  selected Project, appears as a normal terminal, and spawns the imported command directly with
  its validated in-Project cwd/env carried as spawn fields. No swe-mux binary sits in the
  resulting process tree, which is what lets a task terminal survive a frozen-app redeploy.
- The browser inserts a client-only `starting terminal…` row/tab before the spawn request
  resolves. Temporary IDs never reach Project persistence or PTY routes; success atomically
  replaces the placeholder with the daemon session, while failure removes it and restores a
  surviving focus target.
- Worktree launch uses the same optimistic lifecycle, and enters it *before* `git worktree add` rather than after.
  Its placeholder is an ordinary tab in the focused pane from the press, focused, naming first the checkout being created and then the setup being run, with the new worktree as its displayed cwd.
  It is a tab rather than the unpanned full-workspace surface it used to be, because an unpanned view is drawn nowhere at all on a phone and worktree bootstrap is the longest wait swe-mux has.
  Refreshes during that wait re-place the client-only leaf without disturbing the pane, except that a launch the operator is watching stays the pane's active tab.
  Resolution replaces the pending identity and follows it only when the pending identity still owns active-session focus, at both the focus and the layout level.
- Spawn preparation runs independent Git identity probes concurrently and briefly caches the
  stable result for repeated launches. Synchronous ConPTY creation runs outside the daemon event
  loop, keeping existing terminals, events, and HTTP responsive during Windows process startup.
- Session-to-commit provenance snapshots the session label, Project, and current `agent_run_id` when a recognized commit tool call begins.
  A conversation rollover before its result arrives therefore cannot move that evidence onto the successor run.
  The durable association and its confidence rules are defined in `features/git.md`.
- Once ConPTY exists, the daemon publishes the in-memory session and returns the spawn response;
  durable Project/history/event registration continues in the background. Transcript imports or
  other SQLite work therefore cannot hide an already-usable terminal. Lifecycle writes that
  depend on the history row wait for this registration internally.
- A create-and-spawn worktree request may provide bounded initial terminal output from its completed setup subprocess.
  `SessionManager.spawn` seeds it before fanout starts, and the supervisor spawn payload seeds the authoritative ring before the harness process starts.
  The setup prelude therefore appears as ordinary scrollback and survives daemon reattachment.
- PTY attach replay and input handling never await attachment/input telemetry persistence.
  Startup metrics separate interactive `server_ready` from `durable_registration` latency.
- xterm device replies are classified separately from human input. Codex OSC 10/11 color
  replies are suppressed in both browser and daemon because its short native-Windows startup
  probe may time out across the browser/WebSocket hop; Codex uses its console-palette fallback
  instead of receiving stale reply bytes as composer input.
- A browser fits xterm after selecting its renderer, then sends `attach_ready` with the active
  columns and rows. The daemon resizes ConPTY before sending replay bytes; older clients may use
  their first `resize` frame or the bounded compatibility timeout. Messages received while
  readiness is pending are processed only after the replay boundary.
- **A reconnect is a delta, not a do-over, whenever the ring can prove coverage.**
  The client tracks the ring position it has parsed up to (each `replay_end` carries the anchor;
  live binary bytes advance it; a `gap` frame invalidates it until the resync re-anchors) and
  offers it as `since` in `attach_ready`.
  A covered gap is answered with exactly the missed bytes into a terminal the client did not
  reset, so the scrollback a pane spent its whole session parsing survives a tab switch, a
  minimize, a phone freeze, and a session-preserving daemon restart.
  Before this, returning to a tab hidden ≥5 s always forced a reconnect (`liveness.ts`), and
  every reconnect reset the terminal and rebuilt it from a 512 KiB window that parses to roughly
  one screen — the "pane only shows a screen's worth after switching tabs" defect.
  The liveness policy is deliberately unchanged: a forced reconnect now usually costs an empty
  delta, and it still verifies a socket a sleeping device silently killed.
  Any doubt about coverage falls back to the full windowed replay, because a wrong delta corrupts
  a terminal silently; the fallback also caps the delta at `attach_replay_bytes`, so continuity
  never reintroduces the unbounded attach parse the budget exists to prevent.
- A flow-control-capable browser acknowledges terminal bytes only after xterm parses them.
  The daemon limits unparsed output per connection to 128 KiB, preventing old repaint traffic from placing typed echo seconds behind the parser queue.
  Attach and resync replay are included, while older browser bundles remain compatible because the capability is negotiated in `attach_ready`.
  Consecutive output chunks already waiting in the sender queue are combined into binary frames up to a 32 KiB target without delaying the first chunk or crossing a control frame.
  Desktop hidden warm panes withhold credit after one bounded window and release it when revealed, so retained busy agents stop consuming the UI thread without disconnecting.
  Mobile mounts no hidden warm panes, preventing offscreen agents from consuming mobile data.
- A session attached from several devices shares one keyboard and one size, and the daemon
  arbitrates both rather than letting the last client to speak decide. Attach, detach and
  reconnect never change process state, and neither does losing an ownership race: refused
  input is echoed back for one replay instead of dropped. Rules, frames and diagnostics:
  `features/terminal-input.md`.
- Desktop shell terminals default to WebGL with DOM fallback.
  Claude terminals are DOM-only because retained alternate-screen panes can return from a hidden compositing interval with a live but corrupt WebGL surface and no context-loss event.
  Codex terminals use the built-in DOM renderer under the `auto` preference, because its full-screen redraws can corrupt off-tail WebGL scrollback, and its rich renderer still reflows the transcript on resize.
  An explicit `webgl` preference reaches Codex, but not Claude or OMP.
  OMP continuously repaints its tail, and deep sessions are repeatedly reconstructed
  from bounded replay as panes leave the warm cache, so a stale WebGL surface looks
  exactly like missing replay until a real resize repairs it.
  A repaint-heavy TUI also wraps the retained ring itself: OMP's spinner alone emits kilobytes per second, so within minutes a bounded replay holds only live-region repaint traffic that parses to a single screen with no scrollback (measured 2026-08-07: 512 KiB of replay from a working OMP session contained zero newlines).
  Recovery is client-requested, because only the client can see what its replay parsed to.
  A pane whose finished replay left less than one screen of scrollback on a normal-screen `repaints_scrollback` harness sends a `repaint` frame; the daemon pulses the PTY one column and restores it (`Session.repaint_current_geometry`), and the child answers by restating its transcript (measured: one pulse elicited a ~460-line re-render), which also repopulates the ring for every later attach.
  The request fires when replay completes on a visible pane and at first reveal for a warm pane that attached hidden, at most once per parsed buffer, and the daemon rate-limits it per session and ignores it for alternate-screen harnesses, whose buffers never hold scrollback.
  An alternate-screen harness has the mirror-image failure and it is repaired without asking the client anything (`replay_needs_repaint`, `_schedule_attach_repaint`).
  Its retained bytes are a differential frame stream rather than a transcript: measured 2026-08-11 across eight live Claude panes, steady-state output addresses only the five rows that change (spinner, context meter, status), while the input box border and prompt are drawn once and then left alone.
  `Session.replay_bytes` restates `?1049h` for a window carrying no toggle of its own, and that sequence clears the alternate buffer, so a bounded window starts the client from a blank screen and can only fill the cells it happens to contain.
  Whether that reconstructs to a whole screen is luck — it holds only when a full repaint landed inside the window — and the same sweep, run twice, found one pane of eight rendering with no border and no prompt, a different pane each run.
  So an attach that served a window rather than everything retained (`Session.replay_window_truncated`) schedules one pulse, unconditionally: a slice of a differential stream carries no evidence of what it is missing, so there is nothing for either end to judge.
  It is deliberately not conditioned on `hidden`, because a warm pane mass-mounted after a Reload UI parses into its buffer while its rendering is paused and is never re-attached on reveal.
  The pulse shares the client-repaint rate window, so a reconnect storm across devices costs the session one restatement rather than one per socket, and is recorded as `terminal_repaint_requested` with `source=daemon` and `reason=truncated_replay`.
  Until this existed, the only repaint path an alternate-screen pane had was a geometry change, which is why the user's workaround was resizing the window by hand.
  A concurrent client resize during the pulse wins: the pulse never restores a stale size over newly arbitrated geometry.
  The pinned xterm 6 WebGL addon carries the upstream missing-buffer-line guard in its runtime bundles, preventing a resize/trim race from aborting a model update and leaving stale glyphs.
  Mobile remains DOM-only.
- Agent startup state uses semantic evidence first. Claude and trusted Codex lifecycle hooks
  normally report `SessionStart`; Codex with disabled/untrusted hooks (or a degraded Claude hook
  path) may use settled live PTY output as a startup-only, lowest-priority readiness signal until
  its first native transcript event.
- Standing engagements (an armed `/loop`, a cron schedule, background tasks, live subagents)
  are annotations on the session (`SessionRecord.standing_activity`), never states: an idle
  session with an armed loop is exactly as idle, and as deliverable, as one without. They are
  run-scoped — every seam that resets observation identity (rollover, heal, promote, demote,
  end) clears them — and TTL'd so a wrong annotation decays on its own. Contract and
  detection: `features/status-detection.md`.
- Claude/Codex promotion preserves the parent PTY's canonical Project and records an atomic
  agent-run history lifecycle.
- Attach, detach, browser reconnect, and pane operations never change process state.
- **Retention and replay are separate budgets.** `scrollback_bytes` (5 MiB) is what the daemon
  keeps; `attach_replay_bytes` (512 KiB) is what a fresh attach or a resync is handed. A client
  must parse every replayed byte before it can render anything, and xterm time-slices that work
  across render frames, so a full-buffer replay is *watched* happening — worst for a CLI in raw
  scrollback mode (Codex), whose bytes are real lines that each allocate and scroll rather than
  repaints of one alternate screen. A trimmed window resumes after the next newline so it can
  never begin inside an escape sequence, and restates the alternate screen when it cut the
  child's `?1049h` off (otherwise a full-screen TUI would repaint into the client's *normal*
  buffer, growing scrollback on every frame — the exact cost the bound removes).
  Both are edited in Settings → Terminals → Scrollback, and both apply live: the replay budget
  is pushed down onto every session already running, because each carries its own copy and
  reads it at attach time, so leaving them behind would be indistinguishable from the setting
  doing nothing (`tests/test_settings_hot_apply.py`).
- Slow subscribers receive a gap frame and deterministic bounded replay.
- Explicit kill attempts adapter-specific graceful exit before process-tree termination.
- **Stand down terminates without dismissal.**
  The session context menu writes durable inactive intent before stopping the process tree, then retains the terminal-state record, sidebar row, and layout leaf.
  Inactive sessions hold no PTY, conhost, observer, ticker, delivery worker, approval grant, or standing activity.
  They are excluded from bulk ended-session removal.
  Agent Resume continues the provider conversation; shell Restart replays the recorded launch contract.
  Both create a fresh session id and replace the inactive layout identity only after the new process is proven live.
- **Removal is asynchronous by contract.** `DELETE /api/sessions/{id}` cannot be quick for a
  live session: it types the backend's graceful exit keys, waits out an agent mid-turn that
  never processes them, force-kills the tree, persists the run, and clears the session's media
  directory.
  The client does not wait for any of it.
  It removes the session from the workspace on sight and settles the request underneath
  (`technical/frontend/workspace-state.md`, "Optimistic session removal").
  Two consequences are load-bearing.
  The daemon keeps reporting a session being killed as live for the whole teardown window, so
  only the client that issued the kill hides it early and every other client converges when the
  request lands.
  And `session_removed` (durable; carries `was_live`, `exit_code`, `stop_ms`, `total_ms`) is the
  only remaining record of what a close actually cost, because no operator watches one happen
  any more.
- `DELETE` against an id the daemon no longer holds answers 404, and callers treat that as
  success rather than as an error.
  A double-tap, a second client that got there first, and a session that exited on its own
  between the click and the request all land there, and all three are the outcome the request
  wanted.
- Once the root exit code is captured, an ended session releases its dead ConPTY host. The
  reader keeps only a thread-local reference long enough to drain final output, and finalization
  cancels any frozen pywinpty read still parked after root exit. Retained scrollback is independent
  of the OS pseudoconsole handle. This lets ended sessions remain visible until explicitly
  dismissed without retaining `OpenConsole.exe`/`conhost.exe`.
- **A graceful session end is a shared typed daemon operation (Phase 7.6).** Distinct from the
  explicit hard kill above, it interrupts the current turn, sends the harness's own exit
  sequence (the adapter `graceful_exit_keys()`, carried on the PTY as `graceful_exit`), waits
  `session_control_graceful_timeout_s` for the CLI to tear itself down cleanly, and falls back
  to the hard stop only on timeout. The browser, CLI, and the MCP `end_session` tool call it,
  and it stamps `SessionRecord.requested_end_reason` before sending the exit sequence so the
  reason survives even a CLI that exits on its own (`interfaces.md`, `mux-mcp.md`).
- **`agent_ended` is a distinct durable end reason.** An agent-initiated end reached through the
  graceful operation - graceful or hard fallback - records `agent_ended`, kept apart from an
  operator `killed`, a CLI-initiated `exited`/`completed`, and a `crashed`. `SessionManager.stop`
  gained a `reason` parameter (default `killed`) and `_mark_ended` prefers
  `requested_end_reason`, so a post-mortem can tell an agent-directed end from an operator one
  (`data-model.md`).
- Ended-session history remains durable.
- Sessions do not own notes.
  Notes are created and managed through the owning Project's flat Notes collection.
- Resume requires a target Project and a valid native identity/transcript. The new process
  starts at the selected Project root and receives a new mux identity.
- **A conversation opens once, and not every holder is a mux session.** Resume refuses a
  conversation held by any live CLI process (`409 conversation_held`), not only one a live pane
  claims. Claude parks a conversation into a background agent that outlives the pane, runs under
  the CLI's own daemon, and keeps the conversation checked out; `--resume` on it prints a refusal
  and exits 1 about 1.5 s later, so the pane the operator would get is dead on arrival. The
  holder is read live from the CLI's own per-process state files
  (`cli_state.conversation_holders`) at the moment of the resume, and only a pid proven to still
  be running counts — a phantom holder would make a resumable conversation permanently
  unresumable, which is worse than the failure it prevents.
- An attach replay restates the DEC private modes the child set once and never repeated
  (`STICKY_PRIVATE_MODES` in `screen_mode.py`, alongside the existing bracketed-paste and
  alternate-screen restatements). Measured from a real Claude start: `?1004`, `?9001`,
  `?2004`, `?2031`, `?1049`, `?1000`, `?1002`, `?1003`, and `?1006` all appear exactly once,
  inside the first 130 bytes. A reconnecting pane resets its terminal, so any of these that
  fell outside the bounded replay window was lost for the rest of the PTY's life. Losing the
  mouse group is what made a phone swipe do nothing: with no mouse modes the browser reports
  no mouse, the drag has nothing to forward, and it falls through to scrolling an alternate
  screen that has no scrollback — silently, and only on sessions deep enough to outgrow the
  window, which is why it read as intermittent. Modes the replay window mentions itself are
  left alone; the child's own most recent word outranks a restatement. `?9001` (win32 input
  mode) is deliberately excluded: it changes how keys are *encoded* rather than what the
  terminal reports back, and nothing observed points at its loss causing harm.
- **Branch** forks a conversation into a new pane and leaves the source pane exactly as it was.
  Two strategies exist, declared per harness (`branch_strategy`), and they differ in who does the
  forking.
- **`transcript_fork`** (Claude) is mux's own, and it is the only one that can branch from a
  chosen *point*. The daemon reads the source conversation, writes a **new** conversation file
  holding its records up to that point (`transcript_fork.py`), and resumes that file in a sibling
  pane. Nothing is typed into anybody's terminal, the source file is opened read-only, and the
  source pane keeps its conversation, its identity and its run. Everything a CLI-mediated fork
  needed therefore does not exist here: no fork to wait for, no release to race, no identity roll,
  and no readiness gate — a pane that is mid-turn, waiting on an approval, or already exited
  branches exactly as well as an idle one. The branch is a genuinely new conversation with its
  own history row, recorded as a `branch` lineage edge carrying the message and cut it was made
  at, because a fork is otherwise indistinguishable from two conversations that share a prefix.
- **`resume_child_thread`** (Codex) asks the CLI instead: `codex resume` opens a child thread
  with its own rollout, diverging from the still-live original. It can only fork from where the
  conversation currently stands, so a request naming a point is refused (`branch_point_unsupported`)
  rather than silently forked from the end. Because it reopens a conversation a live process is
  still on, it keeps the readiness gate (`_branch_block_reason`: mid-turn, awaiting approval,
  ended, or unsent composer text) and the spawn retry that a CLI-mediated fork needs.
- **Where a fork may be cut is a per-point answer, not a per-harness one**
  (`transcript_view.conversation_cut_points`). Every cut lands on a message's own end offset, so
  a fork's last record is always a real conversational record rather than whichever housekeeping
  line the CLI wrote next. A cut that would leave an assistant turn's tool call unanswered is
  refused: the provider rejects such a conversation outright, so a fork made there would not load
  at all. A dialect with no measured rule for that returns no cut points rather than assuming
  every boundary is clean, which is what keeps an unimplemented harness refusing instead of
  writing conversations that fail on open.
- **A fork is independent of the conversation it came from.** Every record's `sessionId` becomes
  the fork's, Claude's absolute references to oversized tool results are repointed at the fork's
  own directory and those files copied, titles are marked so the CLI has no name collision to
  break (the 2026-08-14 incident where a shared title drove the collision resolver to write
  ~57 MB of generated suffixes into two transcripts), and a queued prompt staged against the
  *source* pane is dropped rather than inherited — delivering somebody's queued message into a
  conversation they had not yet decided to have is the one failure mode a branch must not have.
- A branch cut **before** one of the operator's own messages hands that message back. It is
  staged client-side and inserted into the new pane's composer once its replay finishes
  (`branchSeed.ts`), never submitted: re-sending the prompt unedited would repeat the request the
  branch existed to change. Deliberately not the spawn's own `seed_text`, which appends the
  prompt to the CLI's argv and therefore runs it.
- **A branch is called `B<n>-<the conversation it came from>`** (`_branch_pane_name`). The subject
  is the source's *display* name, resolved through `session_titles.py` rather than read off
  `record.name`: those differ for exactly the sessions worth branching, because a session nobody
  renamed shows its generated title while `record.name` is still the spawn default, and reading
  the raw field named the branch `claude-6vried branch` for a conversation the operator knows as
  "Update ABC". A rename outranks a generated title here as everywhere. The ordinal counts the
  branches already cut from that conversation, and an ordinal the subject already carries is
  replaced rather than stacked, so a tree three deep is not called `B1-B2-B1-…`. It is a label,
  not an identity: branches at different depths of one tree can share a number, and nothing reads
  it back. Passing a name makes the pane `auto_named=False`, which is what stops the titler taking
  the name back the moment the branch says its first word.
- The sibling is spawned and then **watched**, and one that exits inside the settle window is
  discarded rather than attached. Whether to retry differs by strategy: a `transcript_fork`
  sibling opens a conversation nothing has ever held, so a refusal will repeat and is reported at
  once, while a `resume_child_thread` sibling is racing a live process and retries.
- **Handing back a pane that spawned dead is the defect, not a degraded success**
  (`spawn_probe.py`, shared by Branch and Resume). A CLI that refuses the conversation it was
  given does not fail its spawn: it starts, prints one line, and exits *after* the response that
  announced success, so the operator gets a grey pane, no message and no log line. Every flow
  that opens a conversation on the operator's behalf therefore proves the pane survived a short
  window, discards one that did not, and reports the harness's own dying output — cleaned of
  terminal control bytes and otherwise unedited, which is what keeps it working when a CLI
  changes its wording or a harness mux has never seen refuses something.
  The window ends early on positive proof that the pane took what it was given (its own pid
  against the conversation in the CLI's state file); without such proof it is paid in full.
  Whether to retry belongs to the caller: a Codex branch retries because it is racing a live
  process the next attempt is further from, while a refusal that will repeat forever is reported
  instead.
  This is a spawn check, not a health check — a pane that dies later is ordinary lifecycle and
  belongs to the watchdog.
- Terminal environments are built from a scrubbed base (`spawn_contract.base_session_env`):
  parent-Claude session markers (`CLAUDECODE`, `CLAUDE_CODE_CHILD_SESSION`, session
  id/entrypoint/pid/effort, `CLAUDE_JOB_DIR`) are dropped at spawn for every session because a
  daemon relaunched from inside an agent session would otherwise mark every nested `claude` as a
  child session — disabling its transcript saving and with it swe-mux's observation. Deliberate
  user configuration (feature flags, `ANTHROPIC_*`) passes through untouched.
  `CLAUDE_JOB_DIR` is the same leak with a wider blast radius: the CLI reads its presence as
  proof that the process *is* that background job without checking its own session kind, so an
  inherited one makes every pane in every Project adopt one dead agent's name, share its
  `$CLAUDE_JOB_DIR/tmp` scratch dir, and write exit-cause files into a finished job's directory
  (upstream `anthropics/claude-code#86531`).
- Every session is spawned describing the terminal that actually terminates its PTY — the
  browser's xterm.js client, not whatever launched the daemon (`spawn_contract.terminal_env`,
  applied centrally in `SessionManager.spawn`). It forces an xterm-256color / truecolor
  capability and shadows inherited emulator and multiplexer markers (Windows Terminal, Kitty,
  WezTerm, iTerm2, VS Code, tmux, screen, Zellij, CMUX) so a CLI never mistakes the daemon's
  launch context for its own terminal. Without it a frozen, tray-launched daemon inherits no
  `TERM`/`COLORTERM` at all and every pane renders monochrome. This is the lowest-precedence
  layer: an adapter's own env, a shell profile, and a task's env all override it.
  Ambient `FORCE_COLOR` and `CLICOLOR_FORCE` are also scrubbed from the daemon base.
  Agent panes add controlled values back through `session_terminal_env`, while shells and worktree setup subprocesses keep normal pipe semantics.
- Agent harnesses additionally get colour forced (`FORCE_COLOR`, `CLICOLOR_FORCE`;
  `spawn_contract.session_terminal_env` gated on `is_agent_harness`). Node-based CLIs (Claude
  Code via chalk/supports-color) refuse colour unless stdout is detected as a TTY, and swe-mux
  launches agents through a shim → windowed frozen `swe-mux.exe` → `cmd.exe` → CLI chain that
  hides the ConPTY's TTY-ness from that check; Rust CLIs (Codex, OMP) key off `COLORTERM` and
  are unaffected by the TTY gate, but the forcing keeps every agent harness coloured regardless.
  The same base (`base_session_env`) also drops an inherited `NO_COLOR` for agents: it is the
  one launch-context pollutant a session's terminal env cannot override (a merge can add but not
  remove a key), and Codex obeys `NO_COLOR` over `CLICOLOR_FORCE` — so without the drop a daemon
  redeployed from inside an agent session leaves Codex monochrome and makes Node warn `NO_COLOR
  ignored due to FORCE_COLOR`. All of this is scoped to agents on purpose: a plain shell keeps
  honouring pipe semantics (no forced flag leaks ANSI into `cmd > file`) and an inherited
  `NO_COLOR` (no-color.org).
- Session-preserving reload (`pty_supervisor_enabled`, on by default since 2026-08-28): PTYs
  spawn inside the standalone PTY supervisor through a `RemotePtyHost` with the same host contract (spawn/write/resize/
  isalive/exit_status/release/stop). The supervisor keeps the authoritative scrollback and the
  per-session/global reaper Jobs; the daemon mirrors scrollback from the subscription stream
  (attach replay, nested-agent detection, and the PTY-idle watchdog all read the mirror). Each
  session's record snapshot, hook secret, and transcript path are mirrored into the supervisor
  (debounced, deduplicated) so a restarted daemon can rebuild the `Session`, reseed scrollback,
  revalidate provider/transcript ownership, and restart its observer/detection tasks — agents
  mid-turn are never touched. Legacy snapshots without immutable root fields are reconstructed
  from the retained spawn executable/argv. If mutable metadata conflicts with that root identity
  or another live session's transcript claim, adoption repairs the record before publishing it
  and quarantines the misattributed history run. A direct agent returns to its root provider; a
  shell whose promoted run claimed a live sibling returns conservatively to shell/detection.
  Shutdown intent decides the sessions' fate: quit stops and reaps everything as before; detach
  (daemon restart) leaves supervised sessions running — and stops those sessions' tickers
  first, so a daemon that is deliberately going away stops narrating its own disconnection.
  If the supervisor is unreachable at spawn time the daemon falls back to today's in-process
  ConPTY, whose lifetime is daemon-bound as before — but only when it can establish that the
  failure preceded the supervisor reserving the session (see "A spawn whose reply was lost"
  below).
- **The supervisor is the primary recovery path, not the only one.** It cannot survive its own
  death: its kill-on-close Job takes every process tree with it, and both the authoritative
  scrollback and the mirrored metadata are process memory. A supervisor crash, a force close, a
  power loss, a start where no supervisor could be spawned, or `pty_supervisor_enabled` turned
  off therefore leave the next daemon with no idea those sessions existed. A durable registry behind the mirror brings them back as **cold sessions** -
  visible, dead, and resumable - and the same layer is what lets an ended pane stay readable at all.
  Contract, format, and bounds: `features/session-recovery.md`.
- A spawn mirrors the session's initial metadata with the spawn RPC itself, not only through
  the debounced meta sink: a daemon crash inside that ~0.5s window otherwise left the
  supervisor holding a live session with empty metadata, permanently unadoptable and
  reachable only by reaping everything.
  The same spawn RPC carries optional initial worktree-setup scrollback, so no second PTY or marker-file polling protocol is needed.
- **A broken connection is not a dead supervisor.** Only a supervisor whose process is
  actually gone means the kill-on-close Jobs closed and the trees died; a transient socket
  fault leaves sessions running. Treating the second as the first fabricated an exit for
  every live session, recorded false history, and re-adopted them on the next boot. The
  daemon reports `supervisor_state: "lost"` instead, and recovery is a daemon restart.
- **Liveness is three states, not a bool.** `RemotePtyHost.liveness()` answers `alive`,
  `dead`, or `unreachable`, and `isalive()` is "not dead" — an unreachable session is a
  *running* one this daemon cannot see. **A session may be recorded as ended on exactly two
  pieces of evidence: a definitive `pty_exit`, or a supervisor death this daemon confirmed by
  pid.** A dropped socket is neither, so the per-session ticker freezes instead: no state
  transition, no metadata push, no durable end, and a log line naming the session and how long
  it has been out of reach. It resumes by itself if the connection returns. The rule holds for
  a malformed frame too, which is classified and counted as a protocol desync rather than
  folded into "connection lost". Writability is separate from liveness: a broadcast skips an
  unreachable session rather than reporting a delivery whose bytes were discarded.
- **A spawn whose reply was lost is asked about, never assumed.** The supervisor reserves the
  session id before creating the child and finishes the spawn whether or not its reply lands,
  so an in-process fallback on a timed-out spawn RPC could put two agents in one workspace.
  The daemon queries `spawn_status` first: `unknown` (never reserved) is the only definitive
  licence to fall back; `reserved`/`live`/`exited` are adopted, because the connection is
  already subscribed to that session. If the query itself cannot be answered, the supervisor's
  own liveness decides — a confirmed-dead supervisor took any child with it, while a live one
  makes the fallback a coin flip, so the spawn fails instead. A supervisor predating the query
  keeps the old fallback and says so in the log; closing that window is what the deploy
  checkpoint is for.
- **Terminating a supervisor is guarded by identity, not by its name.** The last-resort reap
  behind `kill_server` closes a Job that owns every session tree, so before it fires the target
  pid is checked against the `started_at` in the discovery file (PID+creation-time, as
  everywhere else) and against a command line that is actually a supervisor launch for this
  config. Missing or unreadable evidence declines.
- **Transcript ownership is corroborated, never assumed.** The candidate pool for a session's
  transcript is the backend's *shared* per-cwd directory, which every CLI on the machine writes
  into — a VS Code Claude extension, a scripted `claude -p`, a one-off terminal run. Three
  gates keep another writer's conversation from being adopted (which would rekey
  `native_session_id`, rebind the history row, and stream the outsider's status and tokens
  under this session's identity):
  - **Bind at first observe — by identity evidence, never by elimination.** The
    single-unclaimed-candidate fallback is refused for every backend. Claude does not need
    it: its transcript path is *derived* from the native id mux injected as `--session-id`,
    so the exact-match route always exists. For a backend that mints its own conversation id
    the fallback is not *safe*, and the gates that looked sufficient were measured and are
    not — "created after this run began" and "our PTY produced output when it appeared" both
    pass for an unmanaged CLI, because an agent TUI repaints continuously. Live: an unbound
    Codex pane adopted the rollout of a `codex` started outside mux in the same cwd and
    rekeyed itself onto the stranger's thread. Codex's `session_meta` does not separate them
    either — `originator` betrays only the headless `codex exec` (`codex_exec`/`exec`); an
    interactive outsider reports `codex-tui`/`cli`, exactly like ours.
  - **Codex binds from its own lifecycle hook.** What an outsider cannot forge is a hook: it
    arrives over this session's own loopback ingress authenticated with this session's own
    secret. Trusted Codex hooks report `session_id` on `SessionStart`, so normal binding lands
    before the first turn and transcript discovery can exact-match it. The older
    `agent-turn-complete` `thread-id` remains the compatibility binding path when lifecycle hooks
    are disabled, untrusted, or unavailable (`_bind_native_id_from_hook` accepts both). Whether the id was
    dictated at spawn is an adapter declaration, `assigns_conversation_id`, and is
    deliberately **not** inferred from the shape of the id: mux session ids are UUIDs too, so
    a shape test treats every fresh Codex placeholder as already bound and refuses the only
    evidence that could bind it.
  - **The CLI's own answer wins over any heuristic.** `claude --continue` / `-r <term>` let
    the CLI choose the conversation, so the shim cannot inject or read a `--session-id` and
    promotes with an empty native id (injecting one anyway is what the CLI rejects outright
    with exit 1). The root `SessionStart` hook then arrives over this session's own loopback
    ingress with this session's own secret, which is the strongest available proof of which
    conversation this PTY runs; it fills an *unknown* id only and never overwrites a bound
    one, so a hook cannot rekey a session.
  - **Filesystem switch fallback is Codex-only.** Backends whose adapter reports conversation
    replacement itself (`reports_conversation_rollover` — Claude, via the SessionStart ingress)
    never take the filesystem switch heuristic at all: the CLI's own
    report is strictly stronger evidence, and guessing from mtimes is the one mechanism that
    could latch a session onto a sibling's conversation in a shared cwd. Where the heuristic
    does apply (Codex keeps it because hooks can be unavailable), following a freshly-written
    transcript
    additionally requires that this session's own PTY produced output around the time the
    candidate appeared. An outside CLI leaves our PTY silent, which is what distinguishes it.
  - **Unresolved siblings block.** Another live session in the same cwd makes a fresh
    transcript ambiguous — but only while it *is* ambiguous. A same-backend sibling is ruled
    out per candidate when its own transcript was still being written after the candidate
    appeared (it is demonstrably on its own conversation), or when its PTY produced nothing
    across the candidate's creation (it cannot have written it). Anything else — a sibling
    that went quiet while still talking, or one with no transcript bound yet — keeps blocking,
    because that is indistinguishable from a sibling that just cleared. An unpromoted *shell*
    that has echoed this backend's name blocks unconditionally: its shim-less launch is about
    to create a transcript here, it owns no id or file to rule it out with, and this session's
    2s switch watcher can beat that shell's 0.5s detection loop to the claim.
- **Replacing a bound conversation is a lifecycle transition, not a rebind.** An in-CLI
  `/clear` or `/new` keeps the PTY and replaces the conversation, so the daemon retires the
  agent run and opens a successor (`agent_run_seq`, `agent_conversation_rolled`) rather than
  rekeying the live one — see `backends.md`. The one-way bind above is unchanged: a hook still
  cannot silently rekey a bound session, it can only report that the session it authenticated
  as is now writing somewhere else, which ends the run it was bound to. A root agent's
  `agent_run_id` is otherwise pinned to its session id by adoption; `agent_run_seq > 0` is what
  tells the restart path that a differing run id is the daemon's own successor rather than the
  misattribution it repairs. That trust is bounded: a rolled conversation that a *sibling's*
  root identity claims is corrupt by definition (two panes cannot write one transcript), so
  adoption falls back to the pane's own spawn anchor, quarantines the corrupt run row, and
  clears the roll counter — and a rolled Claude root keeps a standing claim on the
  conversation named by its own mux id, so the rightful owner's own corruption cannot hide
  the conflict. `agent_lifecycle_id` only ever moves on CLI-confirmed rollovers (never on a
  heuristic switch), which is what makes it a trustworthy heal target.
- **Resuming a conversation is the mirror image, and inherits its run.** A rollover is one PTY
  moving to a new conversation; a resume is one conversation moving to a new PTY. Claude's
  `--resume` appends to the same transcript under the same id, so the new pane continues the
  run it resumed rather than opening a second one over one file, and `spawn_agent_run_id` —
  immutable spawn evidence, the counterpart of `agent_run_seq` — is what tells adoption that
  this differing run id is inherited rather than the misattribution it repairs. The inheritance
  lapses on its own: a later rollover mints a run of the pane's own, which no longer matches.
  Two bounds keep it honest. It is refused when a sibling's spawn claim covers the same
  conversation (same rule as a rolled conversation, same fallback to the pane's own anchor).
  And a run id repaired away this way is *dropped, never quarantined* — it names the resumed
  conversation's own row, so quarantining it would delete a conversation's real history over a
  dispute about which conversation this PTY is on. The pane's ownership evidence is unchanged
  by any of this: an unrolled resume still proves its claim through its spawn id, so the sweep
  still never heals it off the conversation it was spawned to continue. Codex mints a new
  rollout id on resume, so there the pane starts a genuinely new conversation and run.
  Before that new run is created, resume resolves the source row's effective visible name: a manual name remains pinned, while an auto-generated title becomes the new pane's initial auto-nameable name instead of falling back to `codex-<id>`.
- **A rollover onto a conversation a live sibling owns is refused outright.** The collision is
  prevented rather than repaired, because repair does not work here: a rollover moves
  `agent_lifecycle_id`, so a pane that followed an in-CLI `/resume` onto a sibling's live
  conversation would then satisfy the ownership test itself, and the sweep — seeing two
  rightful owners — heals neither. Verified live: pane B resumed pane A's conversation from the
  `/resume` picker, `identity_collision_detected` fired in 1.1 s, and both panes then reported
  A's conversation and its tokens indefinitely. The refusal keeps the pane's own identity
  intact, emits `conversation_rollover_refused`, and fails the pane's observation closed
  (`observation_stale_since`) — its CLI genuinely is writing elsewhere, so the pane's status is
  no longer trustworthy even though its identity is. A sibling only counts as the owner when
  its own claim is supported by identity evidence, so deferring to a *misattributed* sibling
  cannot freeze corruption in place.
- **One live session per conversation, continuously enforced.** The state watchdog runs an
  identity sweep each pass as the backstop for corruption that predates the refusal above: any
  two live agent sessions claiming one `(backend, native_id)`
  are logged and emitted as `identity_collision_detected`, and a Claude member whose claim is
  unsupported by identity evidence (its own mux id, its CLI-confirmed lifecycle anchor, or an
  unrolled resume's spawn id) is healed back to its strongest anchor — observer rebound to the
  anchor conversation's deterministic transcript, history row repaired or the corrupt run row
  quarantined (`session_identity_reconciled`, trigger `live_sweep`). Collisions also surface
  in `/api/diagnostics/status-health` as `identity_collisions` and raise its alarm.
- The root process's OS creation time is captured at spawn (`SessionRecord.root_started_at`).
  A pid alone is not an identity on Windows, and exited sessions stay listed with their pid
  intact, so every later consumer of `record.pid` pairs the two.

### Console contention

A session spawned as a shell and promoted around an agent typed into it has *two* processes
that may legitimately own its pseudoconsole over its lifetime - the shell, and the agent
launched from it - and exactly one that may at a time.
The launch chain keeps the shell blocked for the agent's whole life (`backends.md`).
When that fails, both read the console and the user's keystrokes are split between them.

**A shell prompt under a promoted pane has three answers, not two.**
`_confirm_agent_exit` previously asked only "is the transcript quiet", so it could demote or
say nothing, and saying nothing is what it did during the 2026-08-27 incident: the agent was
alive and still writing, every retry saw a busy transcript, the loop gave up, and mux went on
presenting a healthy agent pane over a contended terminal for the rest of the session.
The rules are now pure and ordered (`console_contention.classify_shell_prompt`):

- Inside the promotion grace window, say nothing - the launch itself scrolls a prompt past.
- The CLI is alive and *outside* this pane's process tree ⇒ `agent_orphaned`. Checked first
  because it names a different repair: nothing will reap that process when the pane ends.
- The CLI is alive ⇒ `shell_regained_console`. Liveness outranks the transcript in both
  directions; an exiting CLI writes its last records on the way out, so the transcript is at
  its least quiet exactly when the process has just gone.
- The CLI's pid is known and dead ⇒ demote, as before.
- No pid at all (a shim-less launch, or a harness that publishes no state) ⇒ the transcript
  decides, which is the historical behaviour.

Every measured field is three-valued: `None` means "not measured" and never "false", so a
psutil pass that fails neither demotes a live agent nor reports a dead one as contention.

**Contention never demotes.** The run is not over, and dropping the backend would take the
transcript binding, the token accounting and queue eligibility with it - a worse pane than the
one the user already has. The session keeps its backend and gains a standing
`console_contention` report, which the pane states as its highest-priority notice (it is the
only one in that slot that is a fault rather than a fact) and `GET /api/sessions/{id}/state-log`
serves under `console`, alongside an on-demand process census.

**Two independent detectors reach the same verdict**, because their blind spots differ. The
shim reporting its own child outliving it is direct proof and needs no prompt; a wrapper killed
hard enough to run neither an `atexit` nor a console handler reports nothing, and the
shell-prompt path catches that one. OSC 133 prompt markers are now consumed for a promoted pane
too (they were previously parsed only for shells), so a profile carrying breakpoint markers and
no cwd reporting is covered as well as one carrying OSC 7.

**The promoted CLI's pid comes from the shim first, `cli_state` second.** The shim's report is
the launch itself speaking and exists for every harness; `cli_state` describes a *conversation*,
and a stale one can name a pid Windows has already recycled. Both are cleared at every run seam
(`_reset_console_identity`) for the same reason.

**The launch window is published.** A pane reads as `shell` until the daemon can bind the
conversation - measured at ~10 s on the frozen app - and every rule keyed on the backend answers
for a shell during it, while an agent's composer is on screen and the CLI is running its
terminal capability probes. `SessionRecord.agent_launch_pending` carries the harness the
detector has seen launching, and the browser resolves *input encoding only* against it
(`frontend/src/inputBackend.ts`): Shift+Enter, paste bracketing, the leading-newline repair,
the mobile Enter payload, and protocol-response suppression. Everything the promotion actually
changes - transcript, tokens, resume, branch, width envelope - stays keyed on `backend`,
because those need a bound conversation and this is only evidence one is coming. Two candidates
resolve to nothing rather than a guess, on the same grounds the daemon's own promotion refuses
an ambiguous match.

**A pane-placement hint is a request recorded on the session, never layout** (2026-08-30).
`request_spawn(pane="split_horizontal"|"split_vertical")` stamps `SessionRecord.pane_hint`,
one-shot: panes are per-device browser state on purpose, so the daemon only records what was
asked for, every browser viewing the Project sees the hint on the session row and races
`POST /api/sessions/{id}/pane-hint/claim`, and the claim clears it atomically - the winner
opens the split through the ordinary `openInSplit` path, the losers read an empty hint and do
nothing, and with no browser open the hint simply expires unclaimed while the session appears
in the sidebar as ever. Splitting and starting stay separate verbs; the spawn succeeds
identically whether or not anything ever claims the hint. Only a visible browser tab competes,
so a background tab cannot rearrange a workspace nobody is looking at.

**A promoted pane must be indistinguishable from a directly-spawned one, and was not.**
Measured 2026-08-27 after the promotion work shipped: a shell promoted around a typed `claude`
still submitted on Shift+Enter. The pane's terminal is built in an effect keyed on `session.id`
that deliberately never re-runs on a backend change, so everything it closed over stayed frozen
at `shell` - Shift+Enter and paste bracketing, the "Copy last reply" gate, the scrollback-repaint
request, the Codex column-floor policy, and the DOM-only renderer choice. A directly-spawned agent
never showed it, because its first render already carried the right backend. The repair is
structural rather than per-call-site (`technical/frontend/packages/terminal.md`): the effect may
not read a backend off the captured prop at all, and the renderer - the one choice made once, at
construction - drops its WebGL surface on promotion instead.

## Key files

- `src/swe_mux/session.py`
- `src/swe_mux/console_contention.py` (the rules, pure, plus the process census)
- `src/swe_mux/agent_launcher.py` (the shim: console hold, lifecycle reports)
- `frontend/src/inputBackend.ts`, `frontend/src/consoleContention.ts`
- `src/swe_mux/pty_host.py`
- `src/swe_mux/supervisor.py`
- `src/swe_mux/supervisor_client.py`
- `src/swe_mux/scrollback.py` (`tail_bytes()` reads the end without joining; `tail()` is
  the replay budget; `bytes()` is full retention)
- `src/swe_mux/session_recovery.py`
- `src/swe_mux/git_projects.py`
- `src/swe_mux/spawn_contract.py`
- `src/swe_mux/adapters/`
- `frontend/src/App.tsx`
- `frontend/src/TerminalPane.tsx`

## Relates to

- `session-recovery.md`: what survives when the PTY owner dies too.
- `projects.md`: canonical ownership and Project registration.
- `project-resources.md`: Project-owned notes.
- `history.md`: durable agent-run lifecycle.
- `project-actions.md`: trusted multi-session task launch.
