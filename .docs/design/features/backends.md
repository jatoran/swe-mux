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
  `PermissionRequest`, `Notification`, `Stop`, and `SessionEnd`.
  The settings directory is removed when its owning terminal ends.
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
- PowerShell cwd-integration re-prepends the mux shim directory to PATH after the user's
  `$PROFILE` runs, so profiles that rebuild PATH (registry refresh, version managers) cannot
  silently bypass shim promotion/demotion and hook injection.
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
  `agent_run_seq`, rebinds `native_session_id` **and** `agent_lifecycle_id`, resets every
  provider measurement, and emits `agent_conversation_rolled`. Queue items bound to the
  outgoing run strand; the auto-delivery grant lapses; Branch forks the live conversation.
  Nothing measured on the retired conversation carries forward.
- Two triggers, strongest evidence first. **Claude's own `SessionStart` hook** arrives over the
  session's loopback ingress with the session's own secret and names the conversation the CLI
  is now writing; a reported id that differs from the bound one *is* the rollover, and the
  hook's `source` (`clear`/`resume`/`compact`/`startup`) is recorded as `start_source` but never
  enumerated on — `compact` and `startup` report the unchanged id and so never roll. This path
  is unaffected by sibling sessions. **The transcript-switch watcher** is the fallback for Codex
  (which has no session-start hook) and hookless launches: a quiet observed transcript plus a
  freshly written, unclaimed, PTY-corroborated replacement in the same run cwd.
- If the observed transcript goes quiet while another transcript for the same run cwd is being
  actively written and is not owned by another live session, observation retargets to it and
  re-enters historical catch-up as part of that rollover.
- When the conversation cannot be followed, observation **fails closed** rather than reporting a
  retired conversation as live. The evidence is a hook whose event *necessarily wrote root
  transcript records* — a prompt submitted, a tool run, a turn stopped
  (`_TRANSCRIPT_BACKED_HOOK_EVENTS`) — arriving after the followed transcript has been dead for
  `TRANSCRIPT_STALE_SECONDS`: the CLI ran a turn that landed nowhere we can see. Then
  `observation_stale_since` is set, the transcript loses its authority over hooks (state keeps
  moving), delivery hard-blocks on `transcript_stale`, observers refuse to read it, and the
  session is marked in the UI and the state log. Cleared by the next record read on any followed
  transcript, or by a rollover.
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
