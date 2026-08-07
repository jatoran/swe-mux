# Backend detection and observation

## What it is

- Adapters isolate spawn/resume syntax, transcript discovery, hook wiring, and graceful exit.
- The harness registry declares provider identity, state and measurement sources, delivery etiquette, automation capabilities, built-in tool catalogs, and hook event sets.
- A plain terminal promotes itself when an inherited mux-local harness shim starts the real CLI.
- No UI backend picker or PTY text injection is required for promotion.

## Key concepts

- Mux ID: stable for one PTY lifetime.
- Spawn backend/native ID: immutable identity of the process that owns the PTY.
- Native ID: the harness conversation identity used for history and resume when the harness exposes one.
- Promotion: authenticated `shell -> harness` transition in the same PTY, preserving mux ID, pane, cwd, scrollback, and any user-assigned name.

## Registry contract

`src/swe_mux/harness.py` is the single inventory of agent harnesses.
Every backend-independent consumer asks the registry a capability question instead of comparing names.
The closed `Backend` literal still includes `shell` and every registered harness so `assert_never` makes incomplete provider dispatches fail type checking.

Capability has two independent axes.
`state_sources` declares which ordered evidence channels can report lifecycle state: `hook`, `transcript`, `pty`, and `cli_state`.
`measurement_source` declares whether tokens, context, cost, and model measurements come from a transcript or are unavailable.
Neither axis implies the other.

`HarnessLevel` is a derived presentation tier, not declared capability.

| Level | Derived condition | Product surface |
|---|---|---|
| `launchable` | No state source and no measurement source | PTY launch and input only; the UI states `not observed by mux` |
| `identified` | Transcript measurements without a state source | Conversation identity and measured data, but no lifecycle badge |
| `observed` | At least one state source | Normalized lifecycle state and delivery evidence |
| `hooked` | Hook state without transcript measurements | Ordered or best-effort hook lifecycle according to the harness contract |
| `managed` | Hook state plus transcript measurements | Complete observation and measurement surface |

The daemon publishes the browser-safe projection at `GET /api/harnesses`.
The frontend replaces its startup compatibility seed with that response and gates transcript, measurement, status, launch, queue, and command surfaces from the published capabilities.

## Capability queries

| Question | Registry query | Consumers |
|---|---|---|
| Is this an agent rather than a shell? | `is_agent_harness(name)` or `name in AGENT_BACKENDS` | Session identity, history visibility, process attribution, agent messaging, skills, environment inventory |
| Can mux read normalized transcript state? | `has_observable_transcript(name)` | Observation startup, transcript/history views, branching, title generation, read-aloud, watchdog recovery |
| Can mux submit a prompt through the PTY? | `delivers_prompts_through_pty(name)` | Prompt queue, auto-delivery, voice submission and interruption |
| Does the harness report lifecycle hooks? | `reports_lifecycle_hooks(name)` | Hook identity binding, rollover decisions, hook-reported transcript relocation |
| Which harnesses need an external usage command? | `external_usage_harnesses()` | Usage polling and provider-state creation |
| Which harnesses expose mux-managed accounts? | `provider_account_harnesses()` | Credential inventory, swapping, and quota polling |
| Does the TUI rewrite content already in scrollback? | `repaints_scrollback` (published capability; frontend `repaintsScrollback(name)`) | Terminal renderer selection: repainting harnesses stay on the DOM renderer under the `auto` preference |

`AGENT_BACKENDS` is derived once in `harness.py`; session and voice code do not declare local backend sets.
Provider-account and external-usage iteration derives from independent descriptor capabilities, because a managed harness can report both through its native transcript without exposing mux-managed accounts.
Direct `claude` and `codex` branches remain only where provider data shapes, parser records, authentication, argv, or resume behavior differ.
Adapter construction, shim generation, and launcher dispatch derive from the registry and each descriptor's adapter family.
Executable and argument overrides live in the per-harness `harness_exe` and `harness_args` configuration maps.

## Supporting a new harness

Add one descriptor before adding provider-specific consumers.
The descriptor is the source of truth for all generic surfaces.

- Every harness declares identity and launch fields: `name`, `display_name`, `executable`, `default_args`, `data_home`, `adapter_family`, `config_dir_name`, and `script_base_name`.
- Every harness declares both capability axes: `state_sources` and `measurement_source`.
- Every harness declares conversation behavior: `reports_conversation_rollover`, `assigns_conversation_id`, `resolves_transcript_by_cwd`, `reports_transcript_path`, and any rollout-file prefix.
- Every harness declares PTY delivery etiquette: `submission`, `root_completion`, and `screen`.
- Every harness declares `repaints_scrollback`, and a new harness should declare it `true` unless its TUI provably never rewrites scrollback (alternate-screen TUIs): the flag decides whether `auto` may give the pane the WebGL renderer, and the safe default is the DOM renderer.
- Every observed harness declares non-empty `normalized_events`, a record classifier, and replay fixtures meeting its derived-level corpus floor.
- Every hooked harness declares `native_hooks`, its `hook_events`, a hook installer, and replay hook-step coverage.
- Every transcript-capable harness declares its transcript semantics and measurement parser.
- Every harness declares automation evidence, tool-catalog provenance, and whether historical usage needs an external command.
- Add the harness name to the closed `Backend` literal and handle every new `assert_never` failure explicitly.
- Add provider-specific branches only for real differences in record schema, auth, argv, resume, or TUI behavior.
- Verify the public registry payload and the launchable-harness frontend contract before enabling richer levels.

## Operations

- Claude and Codex continue to use their ordinary shared home directories. Provider account
  selection swaps only the system auth file, so adapters, shims, configuration, skills,
  transcript discovery, and live-process behavior require no account-specific launch path.
- Claude explicit spawn uses `--session-id`; resume uses `--resume`.
- Shell child PATH begins with generated shims that resolve the real executable before
  PATH modification, assign/retain the native ID, inject hooks, then POST promotion
  with the inherited per-session secret.
- Promotion is accepted only while the root session is a shell (plus exact idempotent
  repeats). A direct Claude/Codex root owns its provider for the entire PTY lifetime, and an
  already-promoted shell owns its active lifecycle until the matching demotion. Therefore
  diagnostic or nested `claude`/`codex` invocations cannot promote and then demote their
  parent agent session.
- Executable resolution supports native binaries and package-manager shims. A stale
  configured `codex.exe` falls back through Windows PATHEXT to an installed `codex.cmd`;
  direct ConPTY sessions and nested shell launches resolve npm Codex shims to their underlying
  `node.exe` + `codex.js` entrypoint so JSON config remains one exact argv value. Other
  `.cmd`/`.bat` targets use `COMSPEC`, while `.exe` targets remain direct argv launches.
  Resolution never returns a mux agent shim (identified by content marker, `shim_paths.py`):
  a daemon relaunched from inside a session inherits the session's shim-first PATH, which
  would otherwise wire `MUX_*_EXE` and account login/status commands back at the shim and
  recurse shim → swe-mux.exe → shim, flashing a console window per cycle. The launcher
  re-resolves a shim target to the real CLI and refuses to run it as a last resort. The
  frozen windowed swe-mux.exe also attaches to the invoking shim's console and forwards
  its std streams before spawning the agent, so children run in the caller's terminal
  instead of fresh visible console windows.
- Claude receives atomically generated per-session hook settings for `SessionStart`,
  `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`,
  `PermissionRequest`, `Notification`, `SubagentStart`, `SubagentStop`, `Stop`, and
  `SessionEnd`. The subagent pair drives the `subagents` standing-activity count
  (`status-detection.md`). The settings directory is removed when its owning terminal ends.
- Claude executes hook commands through Bash even on Windows. Generated commands use
  Bash-safe executable paths (for example `/d/.../python.exe` under Git Bash/MSYS), are
  written by atomic replacement, and must never use raw Windows `list2cmdline` output.
- Codex explicit spawn and the shell shim add the stable lifecycle hook set
  (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`,
  `SubagentStart`, `SubagentStop`, `Stop`, `SessionEnd`) as inline `-c` configuration. The
  definitions are additive with hooks from every other Codex config layer, skip an event the
  launch argv explicitly configures, and carry no session secret; authentication comes from the
  inherited `MUX_HOOK_*` environment. Codex asks once to trust the exact command definitions for
  non-managed hooks. swe-mux does not bypass that review or a user/admin hook disable. The older
  `notify` program remains a completion and identity fallback, and resume uses `codex resume`.
  Direct and shim-launched Codex sessions default `tui.alternate_screen="never"`, keeping the
  transcript in native xterm scrollback instead of asking its full-screen TUI to repaint history
  while the viewport is off-tail. An explicit `harness_args["codex"]` or per-launch config value wins for
  that key. The Project Run menu and custom launcher both use this same direct adapter spawn
  path; neither types an agent command into an intermediate shell.
- oh-my-pi explicit spawn, resume, and shell-shim launches materialize a private extension
  package under the mux data directory and add its directory through `--extension <path>`.
  The package copies `src/swe_mux/assets/omp_mux_hook.ts` to `index.ts` and carries a sibling
  `.mcp.json` that registers the mux Streamable HTTP endpoint with that session's bearer token.
  OMP has no per-launch MCP-config flag, and its extension-package MCP loader does not expand
  environment variables, so a shared registration would either fail authentication or leak one
  session's identity into another.
  Mux does not modify project MCP files or `~/.omp/agent/mcp.json`.
  The source is an OMP `ExtensionAPI` factory because installed omp 17.2.10 exposes exact
  approval-request and approval-resolution events only through that current API, not the legacy
  `HookAPI` surface.
  It posts authenticated normalized lifecycle events through `MUX_HOOK_URL`, emits one
  monotonically increasing source sequence, and retries a failed delivery with the same envelope.
  Source launches copy the checked-out asset, while a frozen desktop rebuild copies the entire
  `swe_mux/assets` directory into the bundle, so the standard redeploy flow refreshes the source
  used by newly materialized packages.
  Existing PTYs retain their original argv and require a fresh omp process to load a changed
  extension.
  Mux sets `TERM_SESSION_ID=swe-mux-<mux-id>` for each OMP process.
  Direct OMP launches also receive an explicit xterm-compatible capability environment instead
  of inheriting emulator and multiplexer markers from the terminal that launched the daemon.
  DEC 2026 synchronized output is disabled because a retained byte replay can cross a paint
  transaction boundary, and native image protocols are disabled because mux exposes OMP's text
  fallback rather than an inline-image terminal addon.
  OMP normalizes that value to `apple-swe-mux-<mux-id>` and writes
  `~/.omp/agent/terminal-sessions/<terminal-id>` with the cwd, exact session file, and optional
  `fresh` boundary marker.
  This breadcrumb is the primary pane-to-transcript binding and avoids same-cwd correlation.
  The adapter reads the native id from the session header after the 256-byte title slot, follows
  `previousSessionFiles` after a move, and uses cwd buckets only when the breadcrumb is absent.
  `/new` and `/fork` create a new native id and transcript file, so mux retires the current run
  and binds a new one in the same PTY.
  `/clear` appends `reset_boundary` in the same file and keeps the same native id.
  `/branch` first opens OMP's Session Tree viewer and is not itself a rollover.
  The selected branch is represented by the file's `id` and `parentId` tree, so observation walks
  the active parent chain instead of treating file order as conversation order.
  `/resume` appends to the original conversation file and keeps its native id.
  OMP transcript observation also publishes the latest assistant message's provider and model,
  exact native token/cache/cost measurements, and the active branch's provider-to-account-hash
  credential pins into live and historical session summaries.
  The account hashes are OMP's pseudonymous SHA-256 values and remain linkable.
  OMP deliberately declares neither provider-account management nor an external usage command.
  The descriptor publishes its documented 31 built-in tools and labels the 16 discoverable tools
  that mount under `xd://` when xdev is enabled.
  `read`, `write`, the other essential tools, and the four integration-sensitive discoverable
  tools (`todo`, `ask`, `grep`, and `web_search`) remain top-level.
  The inventory records setting-gated tools as gated rather than claiming they are active.
- OMP's capability providers load authored skills from native `.omp`, imported `.claude` and
  `.codex`, shared `.agent` and `.agents`, project `.github`, and managed-skill roots.
  Extension-package skill roots are deliberately not scanned: mux's only managed extension
  package is its per-session hook package (no skills), and the user's OMP extension
  configuration is OMP-internal state mux does not read.
  Cursor and Cline contribute rules and configuration, not skill directories.
  The Commands surface invokes OMP skills as `/skill:<name>`.
- OMP's ordinary TUI runs on the normal screen and commits transcript rows into native scrollback.
  Fullscreen overlays borrow the alternate screen only for their lifetime.
  Installed OMP 17.2.10 treats native Windows and WSL-hosted output as ConPTY, bounds large writes,
  and uses ConPTY-specific repaint settling without changing mux's bounded replay contract.
  Its startup enables bracketed paste once, so mux's replay prefix restoration is required after
  a deep reconnect and is covered by the same parser path as Claude and Codex.
  Replay also closes a synchronized-output or autowrap-disable sequence only when the retained
  bytes contain a complete unmatched opening sequence, keeping the replay self-contained without
  inserting control bytes into a partial CSI at the live-output boundary.
  OMP is the only integrated harness that sends DECRQM mode queries (`CSI ? Ps $ p`) at startup,
  and those queries crashed the bundled xterm 6.0.0 parser: Rollup dead-code elimination dropped
  the declaration of `requestMode`'s function-local enum while keeping its assignment, a
  strict-mode `ReferenceError` that killed xterm's write loop and rendered every OMP pane black
  (measured 2026-08-06).
  `frontend/scripts/patch-xterm-requestmode.mjs` removes the unused enum before bundling, and
  `frontend/scripts/verify-bundle.mjs` fails both frontend build paths if any emitted asset
  carries the dropped-declaration artifact again.
- The shell shim promotes OMP with an empty native conversation ID because OMP mints that identity.
  The in-process extension and terminal breadcrumb establish the actual conversation, while the
  shim's `finally` block always posts demotion after the child exits.
  The same `agent_launcher.py` path supports non-interactive OMP arguments without introducing a
  second PTY execution mechanism.
  OMP's Python RPC client was evaluated only for a future headless automation consumer and is not
  used for interactive PTY sessions or current mux automation.
  OMP transcript records report messages, branch/reset boundaries, provider and model changes,
  credential pins, native tool calls/results, and explicit compactions.
  They do not report browser rendering, PTY delivery readiness, or approval timing by themselves.
  The in-process extension supplies low-latency lifecycle and approval evidence, while PTY rules
  remain the visible-screen guard for delivery.
- **`tui.raw_output_mode` is the CLI's to decide, not mux's.** It was previously forced to
  `true` alongside the screen-buffer default and is no longer set at all. Raw output suppresses
  Codex's rich transcript rendering, which cost panes their colour (measured 2026-08-05 over a
  512 KB replay window: 26 colour escapes against 6,353 in a Claude pane), their tool-output
  folding, and any visual break between working and answering. Nothing mux reads depends on it:
  the normal-screen prompt (`delivery-readiness.md`), scrollback holding real lines rather than
  repaints of one fixed screen, and xterm owning every scroll all follow from
  `tui.alternate_screen="never"` alone. Codex ships raw output off, so omitting it restores the
  CLI default and leaves `~/.codex/config.toml` in charge. `/raw` toggles it for one live
  session; `harness_args["codex"]` pins it for new ones.
- Hooks provide low-latency state changes. Native transcripts are authoritative fallbacks,
  including when an agent is launched outside a shim or an agent mode omits a hook. Source
  priority arbitrates conflicting evidence within one root turn and is released at the next
  root boundary, so a previously healthy hook cannot permanently suppress transcript fallback.
- A trusted Codex `SessionStart` hook normally establishes startup and conversation identity
  immediately. Hooks may be disabled or still awaiting trust, and Codex may not create its
  rollout until the first submitted turn, so a one-second quiet period after live PTY output
  remains the lowest-priority startup fallback. Any later hook/transcript evidence supersedes it.
- **A Codex rollout may be followed provisionally before its conversation is proven.** Identity
  still comes only from the hook — an outsider cannot forge one, and nothing on disk separates
  our rollout from a `codex` started in the same cwd outside mux (`originator` betrays only the
  headless `codex exec`). But refusing to *read* the file until then meant a fresh pane had no
  transcript and no hook for its entire first turn, so its status could not move at all: measured
  live at 200 s of "ready · turn complete" with the rollout's own `task_started` written 4 s
  after spawn. The sole unclaimed candidate is therefore adopted **for state only** when the
  backend mints its own conversation id, the session is still unbound, and the file was *created*
  around this agent run's start — the last being the gate the earlier analysis lacked, since
  `recent_transcripts` filters on mtime, which any live outsider passes continuously.
- A provisional binding may move turn state, tool detail, and awaiting reasons, and nothing else.
  It may not rekey `native_session_id` (from the file's own `session_meta` or otherwise), write
  the history row, or publish tokens, context, model, or compaction evidence — all of which are
  durable claims that some work was *this* session's. It is also not mirrored into supervisor
  metadata, which a successor daemon would read as an established binding. The worst case for a
  wrong guess is a pane reading "working" while an unrelated codex runs: visible, self-correcting,
  and strictly more conservative for delivery than the "ready" it replaces.
- `SessionStart` normally prevents the provisional path by binding the conversation before the
  observer needs to guess. When lifecycle hooks are unavailable, `agent-turn-complete` resolves
  it. If the hook names the conversation the guess was already
  following, the binding is promoted (`transcript_binding_confirmed`) and the history row is
  finally written; if it names a different one, the guess is discarded
  (`transcript_binding_discarded`) and the observer re-derives by exact match, which exists from
  that moment on. Nothing durable was written under the guess, so the discard is complete.
  `GET /api/sessions/{sid}/state-log` reports `transcript_provisional`.
- When even that guess is unavailable, the PTY screen drives the first turn on its own
  (`status-detection.md` § the unwitnessed session).
- **Every interactive PowerShell session re-asserts the shim directory at the front of PATH
  after the user's `$PROFILE` runs**, so profiles that rebuild PATH (registry refresh,
  version managers) cannot silently bypass shim promotion/demotion and hook injection. The
  guard is unconditional and independent of cwd-integration, which is an opt-in telemetry
  feature nobody would think to enable to keep agent detection working. Bundled with it, a
  `$PROFILE` doing `$env:PATH = [Environment]::GetEnvironmentVariable('Path','Machine') + …`
  cost the default profile every promotion, hook, status and title for the pane's whole life:
  `claude` resolved to the real CLI, so no promote arrived, no `--session-id`/`--settings`
  were injected, and the transcript fallback could not see a launch in a subdirectory either.
  PATH is rebuilt front-first rather than tested for membership, matching the invariant
  `create_agent_shims` establishes at spawn — one shim dir, in front. `-NoProfile` profiles
  are unaffected (nothing clobbers PATH) and a profile carrying its own `-Command`/`-File`
  cannot be wrapped, so it degrades silently; only explicitly requested cwd-integration still
  refuses such a profile outright. cmd.exe runs no startup script and WSL cannot use Windows
  `.cmd` shims at all (`agent-bridge-unavailable`), so neither carries the guard.
- Transcript records that already existed when observation attaches (resume, promotion after
  first activity, retargeting) are historical: they still populate tokens/context/model and
  tool-name correlation but never emit events or drive state. After catch-up the session is
  ready, or working only when history ends in an open turn newer than one minute.
- Transcript truncation or replacement begins a new historical snapshot. This covers Claude
  cancel/revert: byte positions are reset against the replacement file, catch-up reconciles the
  current root state, and an empty replacement clears stale working/awaiting state.
- Claude local-command user records (`<command-...>`/`<local-command-...>`, `isMeta`) never
  begin a turn; a hook-started turn that produced no model activity is closed by its own
  command record. `[Request interrupted by user...]` records abort the turn and may reclaim
  state authority from hooks, which never deliver interrupt evidence. A provider-native
  completion can likewise close its active hook-started turn when the completion hook is missed.
- While a session is promoted, a rendered shell prompt (OSC 7) means the nested CLI exited;
  after a short post-promotion grace and a transcript-quiescence check the daemon demotes the
  session even when no shim demotion arrives. This fallback requires a cwd-integration shell.
- **An in-CLI conversation replacement is a new agent run, not a retarget.** `/clear` (Claude)
  and `/new` (Codex) mint a new native session id and a new transcript file under the same PTY,
  mux session, hook secret, and MCP token. The daemon *rolls the run*: it closes the outgoing
  one exactly as an agent exit does (final token/context figures, its own history row, its own
  transcript path, its own indexed messages), mints a fresh `agent_run_id`, bumps
  `agent_run_seq`, rebinds `native_session_id`, resets every provider measurement, and emits
  `agent_conversation_rolled`. `agent_lifecycle_id` rebinds only on *CLI-confirmed* rollovers
  (Claude); a heuristic transcript switch never moves it, so the anchor stays a trustworthy
  heal target for identity reconciliation. Queue items bound to the
  outgoing run strand; the auto-delivery grant lapses; Branch forks the live conversation.
  Nothing measured on the retired conversation carries forward.
- One trigger per backend, by evidence the backend can actually give. **Claude's own
  `SessionStart` hook** arrives over the session's loopback ingress with the session's own
  secret and names the conversation the CLI is now writing. A reported id that differs from
  the bound one is a rollover **only when it can be this PTY replacing its own
  conversation**: the ingress and the secret authenticate the *session*, not the process,
  and a nested `claude` launched by the session's own tool call inherits the hook wiring
  and speaks over the same channel. Two facts separate the two, and either refuses the roll
  (ledgered and emitted as `conversation_rollover_refused`): `source: "startup"` is a fresh
  process announcing itself — an in-place replacement reports `clear`/`resume`, while
  `compact` keeps the id and never reaches the comparison — and a hook `cwd` that is not
  the session's, because replacing a conversation cannot move the CLI's working directory.
  (Measured live 2026-07-31 before the guard: a session whose task spawned probe children
  rolled onto their conversations 14 times and spent most of its life showing their
  unanswerable "awaiting approval".) Once bound, the session's **state also listens only to
  its own conversation**: a hook naming any other conversation is ledgered
  (`foreign_conversation_hook_ignored`, counted in status health) and dropped without
  refreshing liveness — with one exception. The session's *spawn* conversation (its own mux
  id, minted via `--session-id`) speaking while the record is bound elsewhere is proof the
  identity was stolen, and it heals the binding back (`session_identity_reconciled`,
  trigger `own_conversation_hook`); the retired-conversation set guards the heal, so a
  stale hook spooled before a legitimate `/clear` can never un-clear it. This path
  is unaffected by sibling sessions, and it is Claude's *only* rollover path: adapters declare
  `reports_conversation_rollover` (and, separately, `assigns_conversation_id` — whether mux
  named the conversation at spawn, which decides whether the transcript is *derived* from the
  native id or has to be learned from the CLI's own authenticated hook), and for backends that do, the transcript-switch heuristic is
  never consulted (a guess from mtimes is the one mechanism that could latch onto a sibling's
  conversation in a shared cwd). **The transcript-switch watcher** remains enabled for Codex as
  a fallback when lifecycle hooks are disabled, untrusted, or unavailable: a quiet observed
  transcript plus a freshly written, unclaimed, PTY-corroborated replacement in the same run cwd.
- For Codex, if the observed transcript goes quiet while another transcript for the same run
  cwd is being actively written and is not owned by another live session, observation
  retargets to it and re-enters historical catch-up as part of that rollover. "Quiet" is
  `_transcript_last_write_ts` here too, on both the followed file and on each same-backend
  sibling being cleared of having written the candidate. Read from the timestamp alone the
  precondition was not enforced at all on Windows, so the candidate search ran against an
  actively-written file and only the ownership evidence stood between that and adopting a
  sibling's conversation.
- When the conversation cannot be followed, observation **fails closed** rather than reporting a
  retired conversation as live. The evidence is a hook whose event *necessarily wrote root
  transcript records* — a prompt submitted, a tool run, a turn stopped
  (`_TRANSCRIPT_BACKED_HOOK_EVENTS`) — arriving after the followed transcript has been dead for
  `TRANSCRIPT_STALE_SECONDS`: the CLI ran a turn that landed nowhere we can see. Then
  `observation_stale_since` is set, the transcript loses its authority over hooks (state keeps
  moving), delivery hard-blocks on `transcript_stale`, observers refuse to read it, and the
  session is marked in the UI and the state log. Cleared by the next record read on any followed
  transcript, by the followed file growing again, or by a rollover.
- **A transcript that is missing rather than quiet is the same failure, and used to be
  invisible.** Claude derives a transcript's directory from the CLI's working directory, so
  entering a native worktree moves the file and the resolved path stops existing. There is then
  no timestamp to be quiet, and this guard returned early - "no reading" read as "no evidence" -
  leaving `parser_status` frozen at `ready` from its last successful read, which keeps hooks
  suppressed as redundant to a transcript that can no longer report anything (measured live
  2026-08-06: `idle` latched for four minutes on a session whose screen showed the working
  spinner and whose turn hooks were arriving 8 s apart). A missing followed file alongside a
  recent root turn hook now marks staleness with `reason: transcript_missing`. Without a turn
  hook it stays silent - the observer is aimed before the CLI creates the file, so "missing" on
  its own is an ordinary startup race. Two repairs for the Claude case run first: the
  `transcript_path` the CLI reports in its own hooks (`note_hook_transcript_path`), and failing
  that `_relocated_transcript_candidate` re-deriving the path from the cli-state file's live
  `cwd`. Either re-aims the observer at the same conversation, which is a **relocation, not a
  rollover** (`design/features/status-detection.md`).
- **`resolves_transcript_by_cwd` says whether a conversation's path can move at all.** True for
  Claude, whose transcripts live under `projects/<encoded cwd>/` so a cwd change relocates the
  file; false for Codex, whose rollouts live in a date tree addressed by thread id and never
  move. Only a `True` backend takes the hook-reported relocation path — for the others a
  differing reported path is a report about some other conversation. `locate_transcript(native
  id)` is the matching recovery: it finds a conversation's file without being told a cwd, which
  is what re-binds a moved session at spawn-discovery time and across a daemon restart. Claude
  probes each project slug for `<native id>.jsonl`; Codex answers from the same id-keyed lookup
  its `transcript_path` already performs.
- **"Dead" is measured by observed growth, never by the file's timestamp**
  (`_transcript_last_write_ts`). Windows does not keep a live file's last-write time current:
  measured 2026-08-06, every long-running Codex rollout on the machine reported an mtime frozen
  at the file's *creation* — 290 s to 3.5 h behind content that had grown to 5 MB — with
  `os.stat`, `GetFileAttributesExW`, `FindFirstFileW`, and `GetFileInformationByHandle` all
  returning that same frozen value, so no alternative call exists. `st_size` stayed accurate,
  and Claude transcripts were unaffected in the same survey. The daemon therefore dates writes
  from its own tailer, which already polls the size every 250 ms and stamps
  `Session.transcript_growth_ts` when bytes appear past its attach snapshot; the timestamp
  survives only as a floor for the window before the tailer attached. Trusting the timestamp
  made this guard fire on healthy Codex sessions roughly 90 s into their life and flap from
  then on, which is a **false-safe inversion**: the operator sees `idle · turn complete`
  everywhere while the prompt queue refuses to deliver, and learns to click through the one
  confirmation meant to stop them.
- Growth is tracked separately from record reads because it covers what reads cannot: a partial
  line, or one the parser rejects, is still proof the file is alive, and
  `_record_parser_observation` never fires for it. Bytes already present when the tailer
  attached are replay and are deliberately *not* growth — counting them would suppress
  staleness detection after every daemon restart, on exactly the sessions this guard exists
  for. The stamp describes one file and is discarded when the observer is re-aimed at another
  (`_aim_observer`), but **not** when it re-tails the same file after a fault.
- Retraction is evidence-based, not the negation of the staleness predicate: the claim is
  dropped only when the followed transcript is written *after* the moment it was marked. The
  other two paths that set `observation_stale_since` — a rollover refused because a live sibling
  owns the conversation, and a CLI-reported rollover that could not be adopted — already know
  the CLI is elsewhere, so quiet on the file they abandoned must not clear them.
- The set is matched against the **raw** hook event type, so it must retain Codex's turn notify
  (`agent-turn-complete`) and not only Claude's `Stop` or the normalized `turn_ended`, because it
  is the compatibility path when lifecycle hooks are absent. Claude reports its own rollovers,
  so its observation never needs to be inferred stale; while `agent-turn-complete` was missing
  from the set,
  `last_turn_hook_ts` was never dated and the fail-closed path was unreachable for it. Verified
  live: a Codex pane rolled by `/new` behind a busy same-cwd sibling kept reporting the
  abandoned conversation, with its retired token counts, as live and `idle` for 200 s.
- Which hooks count is the whole correctness of that rule, and "any hook" is wrong.
  `Notification:idle_prompt` fires roughly a minute *after* a turn ends to report that the agent
  is waiting, so it is guaranteed to arrive with no accompanying transcript activity — the exact
  shape being tested for. Keying on it marked every healthy idle agent in the fleet stale 90 s
  after its last turn (8 false positives across 4 sessions on the first live pass, zero true
  ones). Lifecycle hooks and the subagent hooks are excluded for the same reason: `SessionStart`
  carries no turn content, and sidechain records go to their own files, not the root transcript.
  Silence alone is never the trigger — an idle agent is also quiet — and PTY bytes are never the
  evidence, because a cleared screen is presentation, not identity.
- Initial observation and fallback detection apply the same live ownership rule: provider/native
  pairs and normalized transcript paths claimed by another live session are ineligible. Multiple
  unclaimed candidates remain ambiguous and do not promote; newest-file mtime is not ownership
  evidence.
- Normalized lifecycle events carry `scope=root|subagent`. Claude sidechains and Agent/Task
  tool lifecycles, Codex `sub_agent_activity`, and Codex child rollouts are child evidence;
  none can make the root idle or ready.
- Claude is working after user/tool activity, awaiting on permission/elicitation prompts,
  and ready after `Stop`, `turn_duration`, or a final text response whose
  `stop_reason` is `end_turn`.
- Codex is working from `task_started` through `task_complete`, and awaiting for
  execution, patch, or user-input approval requests. `turn_aborted` and
  `thread_rolled_back` both close the active root turn.
- Claude context usage is current input plus cache creation/read input divided by the
  model context window. Codex context usage is `last_token_usage.input_tokens` divided
  by `model_context_window`; cumulative session totals are retained as token counters
  but never displayed as current-window utilization.
- Transcript cwd/time matching remains a fallback for non-shim or unusual launch paths.
  The primary promotion endpoint requires the unexposed per-session hook secret, so
  unrelated browser/tailnet clients cannot claim a terminal.
- The shim posts a matching authenticated demotion when the nested CLI exits. The PTY,
  pane, cwd, and scrollback remain intact, while backend/state/context immediately return
  to shell values and nested-agent detection resumes for the next launch. The shell-prompt
  fallback above covers exits whose shim demotion never arrives.
- Adapters own recent-transcript discovery and native-ID extraction. Codex matches the
  exact native ID when available and uses bounded cwd/time correlation only for new
  sessions whose CLI chooses the native ID internally. Daemon reattachment revalidates this
  observational metadata against immutable root identity before it can drive provider-specific
  status, tokens, context, history, resume, or frontend rendering.
- **A known conversation is located by name, never by recency.** `transcript_path(native_id)`
  answers from the rollout file name and confirms it against the file's own first record, so a
  resumed pane binds immediately; correlation stays the fallback for a pane whose id is still the
  placeholder mux one. Recency cannot serve a resume at all: `codex resume` appends to the
  rollout the thread already owns, so the file is older than the pane, and Windows leaves an open
  file's mtime frozen at creation while it grows (measured: a live 5 MB rollout reporting an mtime
  71 minutes behind its newest record). Resumed Codex panes were therefore unobserved for their
  whole life unless a written turn happened to refresh the mtime — no Transcript tab, no tokens,
  no context, and a history row that reported no native transcript.
- A pane never starts out following a transcript a live pane already holds, whatever named or
  correlated evidence points at it. Codex Branch resumes a *live* conversation deliberately, to
  make the CLI fork a child thread, and the fork's own rollout is what discovery then binds.
- Claude project directories use the CLI's current non-alphanumeric-to-hyphen cwd encoding.
  Codex reads the active `CODEX_HOME` (falling back to `~/.codex`). Child rollouts with
  `parent_thread_id` are excluded from promotion and external-history reconciliation.
- Parser schema v2 publishes recognized/unknown counts and bounded signatures. Sustained
  unknown-record drift degrades semantic capability and forces delivery readiness unknown.
- Operational telemetry has provider-specific parser versions. It records explicit native
  tool calls/results, duration/error evidence, named skill invocations, and compactions;
  unsupported or unknown records remain coverage diagnostics rather than inferred events.

**Hook payloads are UTF-8 and must be decoded as UTF-8.** The shim reads the JSON body
from `sys.stdin.buffer` and decodes explicitly. `sys.stdin.read()` decodes with the
process locale encoding instead, which on Windows is the ANSI code page (cp1252) with
`errors="surrogateescape"` — so every non-ASCII character in every hook payload was
corrupted at ingress. `⚠️` (`E2 9A A0 EF B8 8F`) arrived as `â` `š` `\xa0` `ï` `¸` plus a
*lone surrogate* `\udc8f`, because 0x8F is one of the five bytes cp1252 leaves undefined.
Accents and curly quotes mojibaked silently; anything whose UTF-8 contains 0x81, 0x8D,
0x8F, 0x90 or 0x9D became unencodable and blew up somewhere else entirely — measured
2026-07-31, phone-pasted prompts left three sessions permanently nameless because the
titler's slice encode raised `UnicodeEncodeError` four layers downstream. Text crossing
this boundary is additionally scrubbed by `text_safety.utf8_safe`; see
`automation.md` for why the observer path treats that as a hard requirement.

## Key files

- Harness registry: `src/swe_mux/harness.py`
- Adapters: `src/swe_mux/adapters/`
- Tailer/parsers: `src/swe_mux/observation.py`
- Hook command: `src/swe_mux/hook_client.py`
- Unicode boundary: `src/swe_mux/text_safety.py`
- CLI shims: `src/swe_mux/launchers.py`, `src/swe_mux/agent_launcher.py`
- Promotion lifecycle: `src/swe_mux/session.py`
- Replay/readiness contract: `delivery-readiness.md`
