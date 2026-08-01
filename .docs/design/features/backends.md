# Backend detection and observation

## What it is

- Adapters isolate spawn/resume syntax, transcript discovery, hook wiring, and graceful exit.
- A plain terminal promotes itself when its inherited mux-local `claude` or `codex`
  shim starts the real CLI; no UI backend picker or PTY text injection is required.

## Key concepts

- Mux ID: stable for one PTY lifetime.
- Spawn backend/native ID: immutable identity of the process that owns the PTY.
- Native ID: Claude/Codex transcript identity used for history and resume.
- Promotion: authenticated `shell → claude|codex` in the same PTY, preserving mux ID,
  pane, cwd, scrollback, and any user-assigned name.

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
- Codex explicit spawn receives a `notify` program; resume uses `codex resume`. Direct and
  shim-launched Codex sessions default `tui.alternate_screen="never"` and
  `tui.raw_output_mode=true`, keeping the transcript in native xterm scrollback instead of
  asking its full-screen TUI to repaint history while the viewport is off-tail. An explicit
  `codex_args` or per-launch config value wins for either key. The Project Run menu and custom
  launcher both use this same direct adapter spawn path; neither types an agent command into an
  intermediate shell.
- Hooks provide low-latency state changes. Native transcripts are authoritative fallbacks,
  including when an agent is launched outside a shim or an agent mode omits a hook. Source
  priority arbitrates conflicting evidence within one root turn and is released at the next
  root boundary, so a previously healthy hook cannot permanently suppress transcript fallback.
- Codex has no startup hook and may not create its rollout until the first submitted turn. While
  the state is still `starting`, a one-second quiet period after live PTY output marks the
  interactive prompt ready at the lowest evidence tier. Any later hook/transcript evidence
  supersedes this startup-only fallback.
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
- `agent-turn-complete` resolves it. If the hook names the conversation the guess was already
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
  conversation in a shared cwd). **The transcript-switch watcher** exists for Codex, which has
  no session-start hook: a quiet observed transcript plus a freshly written, unclaimed,
  PTY-corroborated replacement in the same run cwd.
- For Codex, if the observed transcript goes quiet while another transcript for the same run
  cwd is being actively written and is not owned by another live session, observation
  retargets to it and re-enters historical catch-up as part of that rollover.
- When the conversation cannot be followed, observation **fails closed** rather than reporting a
  retired conversation as live. The evidence is a hook whose event *necessarily wrote root
  transcript records* — a prompt submitted, a tool run, a turn stopped
  (`_TRANSCRIPT_BACKED_HOOK_EVENTS`) — arriving after the followed transcript has been dead for
  `TRANSCRIPT_STALE_SECONDS`: the CLI ran a turn that landed nowhere we can see. Then
  `observation_stale_since` is set, the transcript loses its authority over hooks (state keeps
  moving), delivery hard-blocks on `transcript_stale`, observers refuse to read it, and the
  session is marked in the UI and the state log. Cleared by the next record read on any followed
  transcript, or by a rollover.
- The set is matched against the **raw** hook event type, so it must name Codex's turn notify
  (`agent-turn-complete`) and not only Claude's `Stop` or the normalized `turn_ended`. Codex is
  the *only* backend this rule protects — Claude reports its own rollovers, so its observation
  never needs to be inferred stale — and while `agent-turn-complete` was missing from the set,
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
- Claude project directories use the CLI's current non-alphanumeric-to-hyphen cwd encoding.
  Codex reads the active `CODEX_HOME` (falling back to `~/.codex`). Child rollouts with
  `parent_thread_id` are excluded from promotion and external-history reconciliation.
- Parser schema v2 publishes recognized/unknown counts and bounded signatures. Sustained
  unknown-record drift degrades semantic capability and forces delivery readiness unknown.
- Operational telemetry has provider-specific parser versions. It records explicit native
  tool calls/results, duration/error evidence, named skill invocations, and compactions;
  unsupported or unknown records remain coverage diagnostics rather than inferred events.

## Key files

- Adapters: `src/swe_mux/adapters/`
- Tailer/parsers: `src/swe_mux/observation.py`
- Hook command: `src/swe_mux/hook_client.py`
- CLI shims: `src/swe_mux/launchers.py`, `src/swe_mux/agent_launcher.py`
- Promotion lifecycle: `src/swe_mux/session.py`
- Replay/readiness contract: `delivery-readiness.md`
