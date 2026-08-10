# Harness abstraction refactor, then oh-my-pi

Turns the hardcoded `claude`/`codex` backend pair into a declared harness registry with capability
tiers, then lands oh-my-pi (`omp`) as the first harness added through that path.

Steps are ordered by dependency.
Each box is checked off as it lands.

## 1. Harness registry and descriptor

- [x] Create `src/swe_mux/harness.py` holding a frozen `HarnessDescriptor` dataclass.
- [x] Give `HarnessDescriptor` the identity fields: `name`, `display_name`, `executable` config
      key, `default_args` config key, `data_home` resolver.
- [x] Declare capability as two independent axes rather than one ladder: `state_sources` (an
      ordered tuple drawn from `hook`, `transcript`, `pty`, `cli_state`) and `measurement_source`
      (`transcript` or `none`).
      A harness's hooks and its transcript can be strong in opposite directions, so a single
      level cannot rank both.
- [x] Add `HarnessLevel` (`launchable`, `identified`, `observed`, `hooked`, `managed`, ordered and
      comparable) as a value *derived* from the two axes, used only as a display tier and as the
      gate for UI surfaces and corpus floors.
      It is computed, never declared on a descriptor.
- [x] Move the three adapter data flags onto the descriptor: `reports_conversation_rollover`,
      `assigns_conversation_id`, `resolves_transcript_by_cwd`.
      Leave them readable from the adapter until step 2 finishes.
- [x] Move `ADAPTER_DELIVERY_ETIQUETTE` (`src/swe_mux/delivery_readiness.py:73`) onto the descriptor
      as `submission`, `root_completion`, `screen`.
- [x] Move `ADAPTER_CAPABILITIES` (`src/swe_mux/automation.py:155`) onto the descriptor as
      `native_hooks`, `transcript`, `pty`, `normalized_events`.
- [x] Move the tool catalogs `_CLAUDE_TOOLS` and `_CODEX_CAPABILITIES`
      (`src/swe_mux/agent_environment.py`) onto the descriptor, with the
      `documented_catalog` / `runtime_dependent` marker as a descriptor field.
- [x] Move the hook event lists (`CODEX_LIFECYCLE_HOOK_EVENTS` in `adapters/codex.py`, the Claude
      event set in the settings generator) onto the descriptor.
- [x] Add `HARNESSES: dict[str, HarnessDescriptor]` in `harness.py` with `claude` and `codex`
      entries, plus accessors `descriptor(name)`, `agent_harnesses()`, and
      `harnesses_at_least(level)`.
- [x] Write `tests/test_harness_registry.py` asserting: every key in `HARNESSES` matches its
      descriptor's `name`; every descriptor at `observed` or above declares a non-empty
      `normalized_events`; every descriptor's delivery etiquette keys are complete.

## 2. Replace name checks with capability queries

- [x] Delete the duplicate constants `AGENT_BACKENDS` in `src/swe_mux/session.py:210` and
      `src/swe_mux/voice.py:55`; re-export a single one from `harness.py`.
- [x] Convert every `backend not in {"claude", "codex"}` site to the capability it actually means.
      The 43 sites are in `agent_environment.py`, `auto_delivery.py`, `history.py`, `processes.py`,
      `observation.py`, `agent_skills.py`, `prompt_queue.py`, `agent_launcher.py`, `server.py`,
      `agent_messaging.py`, `spawn_contract.py`, `session.py`, `voice.py`.
- [x] For each converted site, record which of these it means: "is an agent, not a shell"
      (`name in agent_harnesses()`), "has an observable transcript" (`level >= observed`),
      "delivers prompts through the PTY" (`descriptor.submission`), or "reports lifecycle hooks"
      (`descriptor.native_hooks`).
- [x] Convert `Provider = Literal["claude", "codex"]` and `PROVIDERS`
      (`src/swe_mux/provider_accounts.py:30,37`) to read from the registry.
      Shipped as `provider_account_harnesses()` over a dedicated
      `provider_account_management` descriptor flag rather than
      `harnesses_at_least("managed")`: omp derives `managed` from its state and
      measurement axes while deliberately declaring no account management, so the
      level alone is the wrong predicate.
- [x] Convert `RefreshState` keying in `src/swe_mux/usage.py:217` and the `["claude", "codex"]`
      target list at `:263` to iterate the registry.
- [x] Grep for remaining literal `"claude"`/`"codex"` in `src/swe_mux/` and confirm each surviving
      one is genuinely harness-specific behaviour, not a membership test.

## 3. Exhaustiveness enforcement

- [x] Define `Backend = Literal["shell", "claude", "codex"]` in `harness.py` and use it as the
      annotation on `record.backend` and on every dispatch parameter.
- [x] Convert the per-backend dispatch branches in `observation.py` (`:776`, `:790`, `:886`,
      `:948`), `operational_telemetry.py` (`:1709`, `:1775`), `reconcile.py` (`:224`, `:246`),
      `agent_launcher.py` (`:227`), `agent_skills.py` (`:539`), `agent_environment.py` (`:434`,
      `:501`, `:805`, `:960`, `:1063`, `:1077`), and `cli_state.py` (`:165`) into
      if/elif chains terminated by `typing.assert_never`.
- [x] Confirm `uv run mypy` fails when a new member is added to `Backend` without handling, by
      temporarily adding one.
- [x] Add `tests/test_harness_registry.py` cases asserting every descriptor at `observed` has a
      registered record classifier, and every descriptor at `hooked` has a registered hook
      installer.

## 4. Harness families

- [x] Parameterize `ClaudeAdapter` on `config_dir_name`, `script_base_name`, and executable so a
      Claude-Code-compatible CLI is a constructor call, not a subclass.
- [x] Parameterize `CodexAdapter` on its data-home resolver and rollout file-name prefix.
- [x] Update the adapter construction in `src/swe_mux/server.py:870-876` to build from descriptors
      instead of positional literals.
- [x] Add a test constructing a second Claude-family adapter against a temp config dir and
      asserting its settings path, shim name, and transcript directory do not collide with
      Claude's.

## 5. Transcript path from the harness

- [x] Preserve `transcript_path` through the normalized hook payload and daemon ingress, read from
      Claude's `transcript_path`; Codex currently advertises no equivalent.
- [x] Add `HarnessDescriptor.reports_transcript_path`; when true, prefer the hook-reported path
      over `adapter.transcript_path(native_id, cwd)` for binding.
- [x] Keep the computed path as the fallback for the pre-first-hook window and for harnesses that
      do not report one.
- [x] Add a replay-corpus scenario where the hook reports a path outside the computed directory
      (the worktree-relocation shape) and assert the observer follows the reported path without
      marking staleness.

## 6. Configuration generalization

- [x] Replace `claude_exe` / `codex_exe` / `claude_args` / `codex_args`
      (`src/swe_mux/config.py:252-255`) with `harness_exe: dict[str, str]` and
      `harness_args: dict[str, list[str]]`, defaulting from the registry.
- [x] Keep the four old keys readable for one release and migrate them into the new dicts on load;
      add the migration to the config test.
- [x] Replace `ccusage_claude_command` / `ccusage_codex_command` (`:369-372`) with
      `usage_commands: dict[str, list[str]]`, and add a descriptor field marking whether a harness
      needs an external usage command at all.
- [x] Update the sanitizer field lists at `config.py:630-633` and `:649` to iterate the dicts.

## 7. Shim and launcher generalization

- [x] Change `create_agent_shims` (`src/swe_mux/launchers.py:58`) to iterate `agent_harnesses()`
      instead of the literal `("claude", "codex")` tuple, emitting `MUX_<NAME>_EXE` and
      `MUX_<NAME>_ARGS` per harness.
- [x] Change `src/swe_mux/agent_launcher.py:223` to validate `sys.argv[1]` against the registry and
      dispatch to a per-descriptor launch builder.
- [x] Confirm `shim_paths.which_real` and the shim self-invocation guard still identify every
      generated shim by content marker.
- [x] Update the PowerShell PATH re-assertion guard so it covers the generated shim directory
      generically rather than naming the two CLIs.

## 8. Frontend generalization

- [x] Add the harness registry to the state the daemon publishes, including each harness's level,
      display name, and capability flags.
- [x] Replace hardcoded backend literals in `frontend/src/` with lookups against that payload.
      Files carrying them: `agentContext.ts`, `agentEnvironment.ts`, `AgentEnvironmentTab.tsx`,
      `agentTargets.ts`, `App.tsx`, `commandRail.ts`, `CommandsTab.tsx`, `HistoryBrowser.tsx`,
      `mobileTerminalIme.ts`, `ProjectRunMenu.tsx`, `ProjectsManager.tsx`, `PromptLibrary.tsx`,
      `PromptsTab.tsx`, `ProviderAccounts.tsx`, `QueuePane.tsx`, `resumeCommand.ts`,
      `SendToAgentPicker.tsx`, `sessionAttention.ts`, `sessionStatus.ts`, `Settings.tsx`.
      A 2026-08-06 audit found three stragglers, all fixed the same day: `agentContext.ts`
      still typed providers as a closed union, `resumeCommand.ts` had no omp branch, and
      `TerminalPane.tsx` gated Branch/copy-resume on names with claude/codex-only tooltips.
      Per-name branches that survive (`resumeCommand`, the Branch gate) encode genuinely
      harness-specific behavior - the resume argv and the server's implemented branch
      strategies - and say so in place; the server now also refuses `branch_unsupported`
      itself, because resuming a live OMP conversation would interleave two writers into
      one session file.
- [x] Gate each agent-specific surface on the harness level rather than on the name, so a
      `launchable` harness renders a terminal with no Transcript tab, no token readout, and no
      status badge instead of rendering an empty or wrong one.
- [x] Add an explicit "not observed by mux" affordance for harnesses below `observed`, so an
      unsupported surface is a stated fact rather than a silent default.

## 9. Regression corpus generalization

- [x] Replace the hardcoded `fixture_counts = {"claude": 0, "codex": 0}` in
      `tests/test_detection_replay.py:66` with a dict built from the registry.
- [x] Replace the fixed `>= 5` floors at `:82-83` with a per-level requirement: every harness at
      `observed` needs a minimum scenario count, every harness at `hooked` needs hook-step
      coverage.
- [x] Add a test asserting every registered harness at `observed` or above has at least one fixture
      whose `backend` names it, so a new harness cannot ship observation with zero scenarios.
- [x] Scope the corpus-wide matrix assertions at `:98-114` and `:156-167` so a harness that cannot
      produce a given evidence kind declares that on its descriptor instead of weakening the
      global assertion.

## 10. Documentation of the abstraction

- [x] Rewrite `.docs/design/features/backends.md` around the descriptor, the capability levels, and
      the registry, keeping the measured findings it already records.
- [x] Add a "supporting a new harness" section listing the descriptor fields required per level.
- [x] Update `.docs/CLAUDE.md` routing so harness-registry changes route to `backends.md` plus this
      document.
- [x] Update `.docs/design/features/provider-accounts.md`, `usage.md`, `delivery-readiness.md`, and
      `automation.md` where they name the two backends as a closed set.

## 11. Status contract: turn-boundary authority separated from measurement confidence

Precedence does not move.
The transcript still owns turn boundaries whenever it is actually reporting, because hooks are an
unordered retried side channel and a late `PreToolUse` landing after an end-of-turn would strand a
finished session as "working".
What changes is that `parser_status` stops standing in for "is the transcript reporting".

- [x] Add `hook_ordering_guarantee` to `HarnessDescriptor`: true only when the harness's hook
      transport preserves order relative to its own transcript writes.
      False for Claude and Codex, whose hooks are independent retried POSTs.
- [x] Rewrite `_transcript_authoritative()` (`src/swe_mux/observation.py:1117`) so authority derives
      from observed liveness: the followed transcript has grown or yielded a record since the most
      recent `_TRANSCRIPT_BACKED_HOOK_EVENTS` hook.
- [x] Source liveness from the existing `transcript_growth_ts` and `last_turn_hook_ts`.
      Do not introduce a timestamp source: Windows freezes an open file's mtime at creation, so
      growth must stay tailer-observed.
- [x] Narrow `parser_status` to measurement confidence only.
      `degraded` stops publishing tokens, context, and cost and marks those figures stale in the
      UI; it no longer changes who owns turn boundaries.
- [x] Fold the four authority-revoking special cases (`transcript_stale`, `transcript_missing`,
      rollover-refused, rollover-unadoptable) into the liveness predicate so none of them is a
      separate branch.
      Keep the reason strings as diagnostics on the state log.
- [x] Encode the invariant that neither source may suppress the other while it is still reporting,
      and assert it in the replay runner alongside the existing `contract_violations` counter.
- [x] Keep the three cross-source corrections, with a dedicated fixture for each: a transcript
      interrupt record (`[Request interrupted by user...]`) aborts a hook-started turn; a
      provider-native completion closes a turn whose completion hook never fired; an ordered
      transcript record clears a hook-raised approval under the existing post-block slack rule
      (`observation.py:107`).
      The first two initially shipped with unit tests only; replay fixtures
      `claude-interrupt-aborts-hook-turn.json` and
      `claude-native-completion-closes-hook-turn.json` were added 2026-08-06 alongside the
      existing `claude-approval-resume-transcript.json`.
- [x] Add fixtures for the two shapes the old proxy variable mishandled: a healthy parser on a
      transcript that has stopped reporting, and a degraded parser on a transcript that is still
      reporting turn boundaries.
- [x] Re-record the replay-corpus goldens.
      Review every changed `states` stream by hand rather than accepting regenerated output.
- [x] Confirm `test_replay_oracles_have_no_false_safe_or_false_blocked_decisions` still reports
      zero of each.
- [x] Make the derived display level read from `state_sources` so a hook-first harness is not
      forced to claim transcript-derived state to reach `observed`.
- [x] Update `.docs/design/features/status-detection.md` and `delivery-readiness.md` with the
      split, and record why precedence did not move.

## 12. PTY evidence: regions, OSC channels, and an uninformative outcome

The PTY layer stays a fallback and a veto and is not promoted by any of this.
These steps make its readings harder to fool by prose and easier to diagnose when a CLI's chrome
changes under them.

Rules stay a declared table in Python.
Not an external DSL, not a manifest loader, not remote rule updates, not numeric priorities: a
dozen markers do not justify a compiler, and the value here is the region and the predicate
structure rather than a file format.
If markers later multiply across harnesses, the table can move to data without redoing this work.

- [x] Add a `ScreenRegion` vocabulary to `src/swe_mux/session.py` covering only what swe-mux reads
      today: `whole_tail`, `bottom_non_empty_lines(n)`, `after_last_prompt_marker`, `osc_title`,
      `osc_progress`.
- [x] Rewrite `pty_tail_state` (`session.py:919`) and `pty_tail_waiting_on_background` (`:886`) to
      use `after_last_prompt_marker` instead of comparing `rfind` positions by hand.
      Both already compute that region inline, so naming it removes code.
- [x] Convert `PTY_WORKING_MARKERS`, `PTY_APPROVAL_MARKERS`, `PTY_IDLE_MARKERS`, and
      `PTY_BACKGROUND_WAIT_MARKERS` into one declared rule table, each rule carrying `id`, `state`,
      `region`, and a predicate.
      Conflicts resolve by declared order, first match wins.
- [x] Drop the "every marker must name a count" requirement (`session.py:855`) once markers are
      region-scoped.
      It was a proxy for "this is a footer"; a region states that directly.
- [x] Extract OSC 0/2 window titles and OSC 9;4 / OSC 4 progress from the PTY byte stream alongside
      the existing OSC 7 extraction, reusing the incremental bounded-retention pattern in
      `src/swe_mux/runtime_cwd.py:9`.
- [x] Keep stripping OSC from the text tail in `_normalize_tail_text` (`session.py:912`).
      The title is signal only inside its own region and noise in the body, which is why it is
      stripped today.
- [x] Measure what the installed Claude and Codex builds actually emit on those channels before
      writing a single rule against them, and record the finding either way.
      A rule built on an unverified channel is the marker-drift problem restated.
- [x] Add OSC rules only for channels measurement confirms.
- [x] Add an `uninformative` outcome to the PTY classifier for screens that are agent-owned viewers
      rather than live state: transcript viewer, model picker, resume list.
- [x] Honor `uninformative` at all three PTY consumers so it withholds a reading rather than
      supplying a neutral one: the delivery screen veto
      (`delivery_readiness.DeliveryReadinessTracker._screen_check`), the watchdog recovery paths
      (`pty_idle_prompt`, `pty_working_after_awaiting`), and the unwitnessed-session first-turn
      fallback (`session.session_is_unwitnessed`).
- [x] Add an explain surface returning every evaluated rule, whether it matched, its region, and a
      bounded preview of the region text.
      The rule walk already exists; recording what it considered is the addition.
- [x] Expose explain on the existing diagnostic path (the state-log companion or the diagnostic
      bundle) so a drifted marker is diagnosable without attaching a debugger.
- [x] Add `tests/fixtures/pty_tails/` cases for each new region, including the prose false-positive
      the count requirement was guarding against.
- [x] Add a fixture for an agent-owned viewer screen asserting `uninformative` and that no consumer
      moves state.

## 13. oh-my-pi: descriptor and launch

Measured against installed omp 17.2.10 on Windows ConPTY on 2026-08-06.
The idle and model-picker rules are pinned to scrubbed captures, and the live probe accepted `/help` before exiting without forced termination on Ctrl+C followed by Ctrl+D.
The build emitted OSC 0 title writes but no OSC 2, OSC 9;4, or OSC 4 sequences.

- [x] Add an `omp` entry to `HARNESSES` at level `launchable`: executable `omp`, display name
      `oh-my-pi`, submission `terminal_line`, screen `normal`.
- [x] Create `src/swe_mux/adapters/omp.py` with `spawn_spec` (`omp`) and `resume_spec`
      (`omp --resume <id>`).
- [x] Determine and record omp's graceful-exit key sequence; set `graceful_exit_keys` accordingly.
- [x] Set `assigns_conversation_id = False` (omp mints its own id; `--provider-session-id` is a
      provider-side value, not the local session id).
- [x] Set `resolves_transcript_by_cwd = True` (session directory is selected from the cwd).
- [x] Register `omp` in the adapter map in `src/swe_mux/server.py` and generate its shim.
- [x] Add omp PTY rules in the section 12 shape, against captured tails rather than from reading
      its source.
- [x] Verify a session spawns, renders in a pane, accepts input, and exits cleanly with no
      transcript binding.
      "Renders in a pane" was false when first checked: every OMP pane rendered black
      because omp is the only harness that sends DECRQM at startup, and the production
      bundler had corrupted xterm 6.0.0's `requestMode` handler into a strict-mode
      `ReferenceError` that killed the write loop. Root-caused and fixed 2026-08-06:
      `frontend/scripts/patch-xterm-requestmode.mjs` (postinstall patch),
      `frontend/scripts/verify-bundle.mjs` (both build paths refuse the artifact class),
      `frontend/test/renderer/decrqm.spec.ts` (renderer regression), verified live against
      the daemon through a preview of the patched bundle.

## 14. oh-my-pi: transcript binding

The checked-in omp `session.md` still describes the short-lived hashed bucket layout, but current 17.2.10 source and installed output use cwd-encoded buckets and migrate the hashed 17.2.5-17.2.8 layout for compatibility.
Binding is therefore breadcrumb-first, supports both directory layouts for fallback discovery, and never relies on the stale documentation alone.
The live resume probe reopened native session `019fd947-f99d-7000-979d-9da1b267cda2` at the identical path and grew the file from 2,234 to 3,363 bytes.

- [x] Set a stable per-pane `TERM_SESSION_ID` in the session environment so omp's `getTerminalId()`
      (`packages/tui/src/ttyid.ts`) resolves to a mux-controlled value.
      Do not use `CMUX_SURFACE_ID`: it also trips `isInsideTerminalMultiplexer()` and routes
      notifications through a `cmux notify` subprocess.
- [x] Implement `locate_transcript` by reading `~/.omp/agent/terminal-sessions/<terminal-id>`,
      whose contents are `cwd\nsessionFile\n` plus an optional `fresh` third line.
- [x] Implement `transcript_path` from the breadcrumb, falling back to the current cwd-encoded
      bucket and the hashed compatibility bucket when no breadcrumb exists.
- [x] Handle the `fresh` marker: it names a `/new` boundary whose JSONL is not yet materialized.
      Treat it as the conversation-rollover signal rather than waiting for a new file to appear.
- [x] Implement `transcript_native_id` by reading the session header (`type: "session"`, field
      `id`) from the first logical entry, skipping the fixed-width 256-byte title slot.
- [x] Handle `previousSessionFiles` in the header so a relocated session is followed rather than
      reported missing.
- [x] Implement `recent_transcripts` and `await_transcript` against both supported session-directory
      layouts.
- [x] Implement `resume_continues_conversation`; confirm against a live resume whether `--resume`
      appends to the original file.
- [x] Verify the derived level reaches `identified`: history row written, Transcript tab populated,
      resume reopens the same conversation.

## 15. oh-my-pi: observation

- [x] Add an omp record classifier to `src/swe_mux/observation.py` covering the `SessionEntry`
      union: `message`, `thinking_level_change`, `model_change`, `service_tier_change`,
      `compaction`, `branch_summary`, `reset_boundary`, `custom`, `custom_message`, `label`,
      `title_change`, `ttsr_injection`, `credential_pin`, `session_init`, `mode_change`.
- [x] Derive turn boundaries from `message` entries and their roles; treat `reset_boundary`
      (written by `/clear`) as a turn boundary within the same file, not a rollover.
- [x] Read tokens from `message.usage` (`input`, `output`, `cacheRead`, `cacheWrite`) and cost from
      `message.usage.cost.total`; wire cost through as a new per-session figure.
- [x] Read context utilization from the model's context window and the message's input totals.
- [x] Consume the `tool_execution_start` custom record for in-flight tool detail, and `session_exit`
      (with `pendingToolCalls`) for interrupted-turn reconstruction on resume.
- [x] Map standing activity: `task` tool calls to `subagents`, `hub` background jobs to
      `background_tasks`.
- [x] Handle the session-file entry tree: entries carry `id`/`parentId` with a mutable leaf
      pointer, so file order is not conversation order after a `/branch`.
- [x] Handle `compaction` entries as context-compacted evidence.
- [x] Record the parser schema version and unknown-record signatures as for the other harnesses.
- [x] Add omp replay-corpus fixtures under `tests/fixtures/detection/v1/` covering: a plain turn,
      a tool-using turn, `/clear`, `/new`, compaction, subagent fan-out via `task`, an interrupted
      turn recovered from `session_exit`, and a resume with historical catch-up.
- [x] Set `state_sources = ("transcript",)` and `measurement_source = "transcript"` on the omp
      descriptor; verify status, tokens, context, and cost against a live session.
      The `("transcript",)` literal was this section's intermediate state; section 16's final
      item moves `hook` ahead, and `pty` is present for the section 13 screen rules, so the
      shipped descriptor reads `("hook", "transcript", "pty")`.

## 16. oh-my-pi: hooks

- [x] Write a TypeScript extension module exporting the current `ExtensionAPI` factory default export, binding
      `pi.on(...)` for `session_start`, `before_agent_start`, `turn_start`, `turn_end`,
      `agent_start`, `agent_end`, `tool_call`, `tool_result`, `session_compact`, `session_switch`,
      `session_branch`, and `session_shutdown`.
      Installed omp 17.2.10 exposes exact approval-request and approval-resolution events only on
      this current API, so the integration deliberately does not use the legacy `HookAPI` surface.
- [x] Have the extension POST normalized events to the daemon's existing hook ingress,
      authenticating with the inherited `MUX_HOOK_*` environment.
- [x] Decide and document where the extension is materialized and how it is passed
      (`--extension <path>` at spawn), including how it is refreshed when mux updates.
- [x] Add the extension source and its build step to packaging so the frozen desktop app ships it.
- [x] Map `tool_call` blocking to the `awaiting`/`approval` state so approval is reported rather
      than inferred.
- [x] Emit a monotonic per-session sequence number from the extension.
      It runs in-process, so unlike Claude's and Codex's shell hooks it can guarantee ordering
      relative to its own transcript writes.
- [x] Set `hook_ordering_guarantee = True` only after that sequence number is verified to be
      monotonic across a reconnect and a retry.
- [x] Add hook-step fixtures to the corpus mirroring the Claude and Codex hook scenarios.
- [x] Move `hook` ahead of `transcript` in the omp descriptor's `state_sources` and verify
      low-latency state transitions and approval reporting.

## 17. oh-my-pi: accounts and usage

- [x] Report only, first: surface `message.provider`, `message.model`, and the `credential_pin`
      entry's provider plus account hash per session; surface cost from the transcript.
- [x] Set the descriptor so omp is excluded from the credential-swap and quota-poll paths that
      `provider_accounts.py` runs for Claude and Codex.
- [x] Do not adopt omp's auth broker for per-session account pinning in this integration
      (`OMP_AUTH_BROKER_ACCOUNT_POOL_FILE`, a JSON provider-to-identityKey map parsed once at
      startup); retain OMP's own routing and report its pseudonymous transcript pins.
- [x] Because the broker is not adopted, do not bind it on its conflicting default port 8765 or
      add another daemon-owned service.
- [x] Because the broker is not adopted, do not generate per-session pool files or consume its
      `/v1/usage`; exact transcript measurement plus ordered hooks already derive `managed`.

## 18. oh-my-pi: remaining integration surfaces

- [x] Register the mux MCP server for omp.
      OMP can import ordinary `.claude` and `.codex` MCP files, but mux registers its server through
      per-launch arguments for those CLIs, so there is nothing for OMP's first-run import to find.
      OMP also has no MCP-config launch flag, and its extension-package MCP loader does not expand
      environment variables.
      Materialize one private extension package per mux session with `index.ts` plus a literal-token
      `.mcp.json`; never modify the project or the user's OMP configuration.
- [x] Verify agent-skill discovery.
      Installed OMP 17.2.10 registers skill providers for native `.omp`, `.claude`, `.codex`,
      `.agent[s]`, `.github`, and managed skills, which the Commands inventory mirrors with
      `/skill:<name>` invocations.
      OMP's extension-package skill roots are deliberately not scanned: the only extension
      package mux manages is its own per-session hook package, which carries no skills, and
      enumerating the user's OMP extension configuration is OMP-internal state mux does not
      read.
      The initial `.cursor` and `.cline` wording was too broad: those providers contribute rules
      and configuration, not skill directories.
- [x] Add omp's 31-tool catalog plus the `xd://` discoverable set to the descriptor's tool
      inventory for the Agent Environment tab.
- [x] Add shell-shim promotion for `omp` and confirm demotion fires when the CLI exits.
      Promotion deliberately reports an empty native ID, and demotion is protected by the launcher's
      `finally` block.
- [x] Add omp to the headless launch path in `agent_launcher.py`; evaluate `omp --mode rpc` with the
      first-party `omp-rpc` Python client for automation runs only, not for PTY sessions.
      The common launcher accepts OMP's non-interactive arguments and retains the PTY surface.
      Do not add the RPC client until mux has a concrete non-PTY automation consumer, because RPC
      cannot represent the terminal session mux is responsible for owning.
- [x] Confirm omp's TUI behaves in ConPTY: alternate screen is used only for fullscreen overlays,
      the transcript lands in native scrollback, and `isConPTYHosted` detection does not conflict
      with mux's scrollback replay.
      Installed OMP 17.2.10 returns true for native Windows and WSL-hosted ConPTY, bounds large
      writes, and uses a normal-screen append-only transcript with temporary alternate-screen
      overlays.
- [x] Verify bracketed-paste survives a deep-session replay for omp, as for the other harnesses.
      The OMP-specific replay regression feeds its measured one-time DECSET 2004 startup toggle,
      cuts it out of a deep replay window, and verifies mux restores it before replayed output.

## 18.5. Deployment and proof checkpoint

Stop here and review the runtime transition with the user before starting section 19.
Do not infer that a supervisor shutdown is required merely because the daemon, adapter, or packaged assets changed.

- [x] Confirm whether the primary checkout is serving source or the frozen desktop bundle by comparing the live and source frontend asset hashes.
      Both currently name `assets/index-DRyG1rbd.css`, but the port-owning process is
      `D:\PROJECTS\swe-mux\dist\swe-mux\swe-mux.exe`, so the active daemon is definitively the
      frozen desktop bundle and the equal hashes only show that its last frontend build matches
      the gitignored source-static build.
- [x] Review the final diff against the supervisor source closure documented in `SESSION_PRESERVING_RELOAD.md` and determine whether the supervisor binary itself changed.
      None of the eight `SUPERVISOR_SOURCES` paths changed, and the current source hash exactly
      matches `dist/swe-mux-supervisor/.source-hash`
      (`2b9231d4cb9966d7b8833579f49e9e4aa67199913d39286653dbaf0dccfbfa72`).
      `supervisor_bundle_current()` returns true.
- [x] If the supervisor source closure did not change, keep the supervisor and all existing PTYs alive and use the frozen desktop app redeploy flow for the daemon, frontend, and packaged omp extension.
      Every packaged update used `POST /api/daemon/redeploy`; supervisor PID 13536 stayed alive and the retained PTYs survived.
- [x] If the supervisor source closure did change, identify every live session that would be reaped, discuss an intentional full stop with the user, and restart the supervisor only after explicit approval.
      Not applicable to this change because the closure did not change.
      No supervisor shutdown or session-reaping restart was requested or performed.
- [x] Explain that existing omp PTYs cannot acquire a new `--extension` argument or newly injected environment after launch, so hook verification requires a fresh omp pane even when all existing sessions survive the redeploy.
      A pre-extension OMP pane remained observable through its transcript and PTY but produced no extension sequence, while a fresh packaged pane loaded the extension and MCP registration.
- [x] Exercise a fresh packaged omp pane through spawn, input, a normal turn, a tool call, an approval request and resolution, compaction, `/clear`, `/new`, `/branch`, resume, daemon reload, and an extension delivery retry.
      The live run covered read and shell tools, explicit shell approval, mux MCP, two accepted compactions, all named conversation commands, reload, graceful exit, and retained-buffer replay.
- [x] Verify hook sequence monotonicity and duplicate suppression across a lost response, daemon reconnect, conversation rollover, and session resume.
      The source sequence reached 66 across the lifecycle and survived daemon adoption.
      Reposting sequence 66 returned `duplicate_or_stale_sequence`, left state unchanged, incremented the duplicate counter, and recorded the rejected sequence and previous watermark.
- [x] Compare live hook, transcript, PTY, token, context, cache, cost, provider, model, credential-pin, history, MCP, skill, promotion, demotion, and bracketed-paste observations with the replay corpus and documented contracts.
      Native telemetry reported eight tool events, zero explicit skill invocations, two compactions, 36 recognized records, zero unknown records, and no truncation.
      The exited history row matched the live provider, model, token/cache totals, cost, context window, final context percentage, compaction count, and transcript path.
- [x] Fix every discrepancy at its owning abstraction, add or update measured fixtures, rerun the focused suites, and repeat the packaged live probe until the observed behavior is stable.
      Fixes covered OMP transcript rendering, backend-scoped PTY rules, Session Tree classification, cross-source compaction deduplication, hook watermark persistence, OMP historical telemetry, and cold attachment to retained ended PTYs.
      The ended-PTY fix addressed the reported pane that rendered while live and became blank after remount: the browser now performs one initial replay attach for ended sessions and still suppresses useless reconnects.
- [x] Proceed to section 19 only after reviewing these results and the remaining close-out work with the user.
      The checkpoint discussion completed, and work resumed only after the user asked to continue.

## 19. Close-out

- [x] Run `.worktree-verify` and confirm the full suite passes.
      The canonical commands passed manually after the Bash wrapper rejected its CRLF checkout: 1,585 backend tests passed, 1 skipped, 8 live-only tests deselected; Ruff, mypy, TypeScript, and all 678 frontend tests passed.
- [x] Update `.docs/design/features/backends.md` with omp's measured behaviour: binding mechanism,
      rollover signals, and what its transcript does and does not report.
- [x] Update `.docs/design/features/usage.md` with transcript-sourced cost.
- [x] Record any omp behaviour that contradicts the assumptions in this document, so the descriptor
      fields it forced are traceable.
      `/branch` opens the Session Tree and is not itself a rollover; `/clear` stays in the same file and native id; `/new` and `/fork` roll to new files and ids; `/resume` appends to the original file.
      Forced compaction can reject a session as too small, and successful compaction emits both hook and transcript evidence with one native id.
      Active history is a spawn/finalization summary rather than a mirror of every live measurement, so final OMP metrics are asserted after rollover or exit.
      Existing PTYs cannot acquire a newly packaged extension, and an ended PTY still needs one browser attach to replay retained output even though it must not reconnect afterward.
