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
The frontend replaces its startup seed with that response and gates transcript, measurement, status, launch, queue, terminal, and command surfaces from the published capabilities.

**The browser holds no per-harness fact of its own.**
Its startup seed is generated from the descriptors into `frontend/src/harnessRegistrySeed.ts` by `packaging/generate_frontend_registry.py`, and `tests/test_harness_registry.py` fails while that file is stale.
The seed was previously hand-maintained and had drifted: opencode was two capability levels below its descriptor with measurement reported as absent, and pi was missing its `pty` state source, because nothing compared the two.
A trait the browser needs therefore has to travel in the payload; `test_every_browser_read_trait_travels_in_the_public_payload` asserts the published capability set, and `tests/test_harness_name_literals.py` fails on a harness name compiled into a frontend module outside a small allowlist.

Adding a field keeps the current payload version because consumers read the keys they know and default the rest.
Version 2 removed the obsolete harness-scoped external usage capability.
The frontend accepts versions 1 and 2 during rolling upgrades.

## Detection and enablement

Detection is machine state, kept out of the descriptor because a `HarnessDescriptor` is identical on every host while installation differs per machine and per configured executable.
`detect_installation(name, executable)` resolves the harness through `shim_paths.which_real`, never `shutil.which`: the daemon prepends `~/.mux/bin` to PATH and writes a shim for every harness, so a plain `which` succeeds on a machine with no such CLI installed, and `which_real` strips those shim directories.
`installed` is the union of two independent signals: the executable resolves, or the harness `data_home()` exists.
`resolved_path` is the real executable the launcher would run, or `null`.

Detection rides the payload only when the daemon supplies it: `GET /api/harnesses` computes `detect_installations_with_versions(config.harness_exe)` off the event loop and passes it to `public_harness_registry(installations)`, which adds `installed`, `resolved_path`, and a best-effort `cli_version` per harness.
The generated seed calls `public_harness_registry()` with no installations and omits those machine facts, because a static file cannot carry one; a missing `installed` reads as "detection not yet known", which the browser treats as enabled for the first paint until the snapshot narrows it.

The CLI version is captured only for the registry payload, never in the hot `detect_installation` enablement path: `probe_cli_version(name, executable)` runs `<cli> --version` best-effort with a short timeout and a brief cache, and never raises.
Each harness entry also carries a static `tested_cli_version` (the last version mux was verified against, from `TESTED_CLI_VERSIONS`; empty by default so the signal never fires on a guessed bound) and a computed `version_untested` when both are known.
`version_is_untested` compares the parsed leading numeric components and fails closed, so an unparseable version reports untested false rather than a false "newer than tested".
A CLI newer than its bound degrades gracefully (the model catalog falls unknown ids back to their family context window, `claude_models.py`); the signal only surfaces that the pairing is untested.

Two per-harness instrumentation toggles, both stored like `harness_enabled` (a dict where an absent key means on) and both restart-scoped because adapters are built once at daemon start:
- `config.harness_mcp_enabled` gates the mux MCP registration. Off, `build_agent_adapter` receives an empty `mcp_url` for that harness, so no MCP server is registered; the harness's status, history, and queue are unaffected. The `mcp` capability (true for every family except pi, which has no MCP client) tells the frontend whether to offer the toggle.
- `config.harness_instrument_enabled` gates the lifecycle hooks. Off, `build_agent_adapter` passes `instrument=False`, and each adapter family skips its hook wiring (Claude omits `--settings`, Codex is built with `notify=False`, and the omp/opencode/pi extension is not injected). This drops the harness to unobserved: no status detection, history capture, or prompt queue for its sessions, which the UI names before applying.

Enablement is a launcher filter with three states.
`config.harness_enabled` holds only explicit user choices; an absent key follows detection.
`enabled_backends(harness_enabled, harness_exe)` resolves the rule: an explicit choice wins in either direction, otherwise detection decides, so a CLI installed later appears on its own, one forced on before install stays on, and one the user owns but hides stays off.
Enablement never gates capability: a disabled harness stays spawnable by an explicit API or CLI call, and every display-name, transcript, status, and history surface keeps seeing all registered harnesses.
The frontend applies the same rule in `harnessRegistry.ts` (`harnessEnabled`, `enabledHarnessNames`, `allBackendNames`, `promptDeliveryHarnesses`); `allHarnessesIncludingDisabled` is the unfiltered accessor the Settings agent section and first-run panel use so a hidden harness can be re-enabled.

The first-run harness panel is gated daemon-side by `config.harness_setup_complete`, not device-local storage, because harness enablement is machine config and a choice made on one device must not reappear on another.
Skipping the panel sets only that flag and writes no `harness_enabled` entries, so a harness installed next week is still picked up by detection.

## Capability queries

| Question | Registry query | Consumers |
|---|---|---|
| Is this an agent rather than a shell? | `is_agent_harness(name)` or `name in AGENT_BACKENDS` | Session identity, history visibility, process attribution, agent messaging, skills, environment inventory |
| Can mux read normalized transcript state? | `has_observable_transcript(name)` | Observation startup, transcript/history views, branching, title generation, read-aloud, watchdog recovery |
| Can mux submit a prompt through the PTY? | `delivers_prompts_through_pty(name)` | Prompt queue, auto-delivery, voice submission and interruption |
| Does the harness report lifecycle hooks? | `reports_lifecycle_hooks(name)` | Hook identity binding, rollover decisions, hook-reported transcript relocation |
| Which harnesses expose mux-managed accounts? | `provider_account_harnesses()` | Credential inventory, swapping, and quota polling |
| Does the TUI rewrite content already in scrollback? | `repaints_scrollback` (backend `repaints_scrollback(name)`; published capability; frontend `repaintsScrollback(name)`) | Terminal renderer selection (repainting harnesses stay on the DOM renderer under `auto`); client-requested transcript restatement after a wrapped-ring replay (`features/sessions.md`) |
| Can the TUI repair its own screen after a width change? | `needs_resize_repaint(name)`, derived from `screen == "alternate"` | Daemon-driven repaint pulse once an arbitrated resize settles (`features/terminal-input.md`) |
| Can a bounded replay of its bytes leave the screen incomplete? | `replay_needs_repaint(name)`, derived from `screen == "alternate"` | Daemon-driven repaint pulse after an attach whose replay was a window (`features/sessions.md`) |
| Is WebGL unusable for this pane under any preference? | `webgl_unsafe` (frontend `webglUnsafe(name)`) | Renderer selection. Distinct from `repaints_scrollback`, which only excludes `auto` and stays overridable |
| Does the CLI publish its own liveness to a state file? | `publishes_cli_state(name)` | The CLI-state layer, and whether a CLI-state reading may veto or override a PTY screen reading |
| Did mux dictate this conversation id? | `assigns_conversation_id(name)` | Identity healing, collision reconciliation, observer transcript rebinding, branch readiness, resume availability |
| Which argv carries a conversation id? | `conversation_id_argv(name)`, `native_id_from_args(name, args)` | Recovering a pane's conversation from its recorded command line |
| What resumes this conversation elsewhere? | `resume_command(name, id)`; published `cli_name` + `resume_argv` | The Copy-resume affordance |
| How is a live conversation forked? | `branch_strategy(name)` | Branch dispatch and its refusal; the browser's branch gate reads the published `branch` capability |
| What does a user type to invoke a skill? | published `skill_invocation_prefix` | Skill inventories and the command rail's injected payload |
| Which root instruction file does the CLI read? | `instruction_harnesses()`, `instruction_file_name`, `global_instruction_parts` | Agent Context inventory and sync |
| Which harness is this command line running? | `harness_for_command(executable, args)` | Recognizing an already-launched agent, including the history backend-mismatch repair |
| Can this harness be driven by the live canary? | `live_canary_harnesses()`, `live_subagent_harnesses()`, `live_telemetry_harnesses()`, from `headless_probes` | The live conformance tier |
| Where does this harness keep its conversations? | `conversation_store_file`, `conversation_store_path(name)` | The store reader and the adapter's measurement read |
| How are its past conversations found? | `conversation_discovery` | `reconcile.scan_external_transcripts`, history backfill |

`AGENT_BACKENDS` is derived once in `harness.py`; session and voice code do not declare local backend sets.
Provider-account iteration derives from its descriptor capability.
Historical usage source discovery belongs to the independent ccusage collector and is not a harness capability.
Direct `claude` and `codex` branches remain only where provider data shapes, parser records, authentication, argv, or resume behavior differ.
Adapter construction, shim generation, and launcher dispatch derive from the registry and each descriptor's adapter family.
Executable and argument overrides live in the per-harness `harness_exe` and `harness_args` configuration maps.
Those are the harness-wide defaults.
A named alternative for one harness is a launch profile, which contributes a second argument slot between `harness_args` and whatever the launch itself asked for (`launch-profiles.md`).

## Where a harness deviation lives

A harness differs from its neighbours along independent axes, and each axis has exactly one declared home.
Keeping them separate is what lets a new harness reuse most of the system: pi needed its own adapter and none of its own reader, which is only expressible because launch and parsing are different questions.

| Axis | Declared as | Answers | Adding a harness that matches an existing one costs |
|---|---|---|---|
| How to launch, resume, and locate it | `adapter_family` + an adapter class | argv, session-file discovery, per-session materialization, graceful exit | a constructor call |
| How to read its conversation | `transcript_dialect` | which record reader parses it | nothing |
| What evidence it can produce | `state_sources`, `measurement_source`, `normalized_events` | which layers may move state, where measurements come from | nothing |
| What its TUI does to the screen | `screen`, `repaints_scrollback` | renderer choice, resize repaint, replay handling | nothing |
| What it exposes | `tool_catalog`, `hook_events`, `config_dir_name` | Agent Environment and Commands surfaces | a descriptor field |

`adapter_family` and `transcript_dialect` are deliberately not one field.
oh-my-pi and upstream pi share a dialect and not a family: the same reader parses both, while their launch paths diverge on session-directory encoding, resume flag, breadcrumb availability, and environment.
Collapsing them would force pi to either duplicate a reader it does not need or inherit a discovery path that silently finds nothing.

Every consumer that parses records dispatches on `transcript_dialect` and terminates in `assert_never`.
Consumers that branch on the harness *name* do so only for behaviour that is genuinely unique to that harness, and those branches also terminate in `assert_never` so a new harness is a type error rather than a silent fall-through.

Three failure modes are specifically guarded, because all three produce a plausible wrong answer rather than an error:

- **A missing branch.** `assert_never` in every dispatch makes it a type error.
- **A branch that exists and returns nothing.** `tests/test_harness_registry.py` requires every declared dialect to parse a representative record of its own shape into a message, and requires a harness declaring no dialect to declare no transcript evidence either.
  The empty Transcript tab pi shipped with was exactly this shape: the reader was reached, produced nothing, and no test, type error, or log line reported it.
- **An opt-in special case with an implicit `else`.** A consumer written as `if backend == <one harness>` gives every other harness a default written for somebody else, and `assert_never` cannot see it because there is no dispatch to be exhaustive over.
  This is the expensive shape, and the fix is always the same: declare the fact on `HarnessDescriptor` and read it back.
  `tests/test_harness_name_literals.py` fails on a harness name outside a small allowlist, so the shape cannot regrow.

  **That allowlist is scoped per function, not per module, and the reason is a caught regression.**
  Its first version exempted whole files, and `reconcile.py` was exempted as "assert_never anchored" on the strength of one anchored dispatch while `scan_external_transcripts` in the same file hardcoded a two-vendor tuple with no anchor at all.
  Retroactive discovery therefore existed for two harnesses out of five, silently, and the lint that should have caught it had vouched for the file.
  A module-wide exemption is only correct when the whole module is about one harness (an adapter) or about a closed set the type system holds (`provider_accounts` and its `ManagedProvider`); everywhere else the unit is the function, and `test_every_allowlist_entry_still_points_at_real_code` fails when an exempted function is renamed or deleted.

## Supporting a new harness

Add one descriptor before adding provider-specific consumers.
The descriptor is the source of truth for all generic surfaces.

- Every harness declares identity and launch fields: `name`, `display_name`, `executable`, `default_args`, `data_home`, `adapter_family`, `config_dir_name`, and `script_base_name`.
- Every harness declares `reserved_launch_args`: the argv its adapter builds for itself, which a user-authored launch profile may not set.
  Declared rather than inferred because the consequence of missing one is silent and total: a profile passing its own `--settings` replaces the file holding a pane's hook identity, so the CLI runs, the pane looks healthy, and the session is never observed again.
  An entry ending in `=` or `.` matches by prefix, which names a value-carrying config override without reserving the flag that introduces it.
- Every harness declares `transcript_dialect`: the reader that parses its records, or `None` when it writes none.
  Reuse an existing dialect whenever the records are the same shape; a new dialect obliges a new reader branch and a sample record in the registry test.
- An npm-distributed harness needs no launch special-casing on Windows: `resolve_npm_shim_pty_command` reads the `.cmd` shim and resolves it to the package's own executable or to Node plus its entrypoint, because ConPTY cannot execute a batch shim.
  Resolve executables with `shim_paths.which_real`, never `shutil.which`, which answers with mux's own generated shim.
- Every harness declares both capability axes: `state_sources` and `measurement_source`.
- Every harness declares conversation behavior: `reports_conversation_rollover`, `assigns_conversation_id`, `resolves_transcript_by_cwd`, `reports_transcript_path`, and any rollout-file prefix.
- Every harness declares PTY delivery etiquette: `submission`, `root_completion`, and `screen`.
- Every harness declares `repaints_scrollback`, and a new harness should declare it `true` unless its TUI provably never rewrites scrollback: the flag decides whether `auto` may give the pane the WebGL renderer, and the safe default is the DOM renderer.
  The three repaint traits answer different questions and must not be merged: `repaints_scrollback` asks whether a harness floods the retained ring and can replay to an empty-looking screen, `needs_resize_repaint` asks whether it can repair its own screen after a resize, and `replay_needs_repaint` asks whether a bounded replay of its bytes can reconstruct to a partial screen. Claude is `false` for the first and `true` for the other two.
  The last two are both derived from `screen == "alternate"` and are deliberately separate names rather than one: they fire on different events (a settled geometry change against an attach), and merging them would put a single docstring in charge of two failures that a future harness could exhibit independently.
  None of the three is a general WebGL-safety claim, which is `webgl_unsafe`: Claude does not repaint scrollback but is still WebGL-unsafe, because its retained alternate-screen surface has a separate live-context corruption mode with no override.
  The same flag gates the daemon's answer to a client `repaint` frame, because a harness that floods the ring with live-region repaint traffic is both the only one whose replay can parse to nothing and the only one that restates its transcript when pulsed (`features/sessions.md`).
- Every harness declares its terminal surface traits: `webgl_unsafe`, `owns_scroll_viewport`, `applies_width_envelope`, `min_desktop_columns`, and `suppresses_late_color_response`.
  These are published to the browser, because a per-harness fact compiled into a frontend module is a second copy of a daemon-owned fact and drifts from it.
- Every harness declares its CLI grammar: `spawn_id_argv`, `resume_argv`, `skill_invocation_prefix`, `instruction_file_name` with `global_instruction_parts`, and `npm_entrypoint` when it ships as an npm package.
  `spawn_id_argv` is non-empty exactly when `assigns_conversation_id` is true, and the descriptor rejects a pair that disagrees.
- Every harness declares `publishes_cli_state`, `branch_strategy` (or `None`), and `requires_direct_entrypoint`.
- Every observed harness declares non-empty `normalized_events`, a record classifier, and replay fixtures meeting its derived-level corpus floor.
  Its own fixtures must produce every normalized event it declares, reach `working` and `idle`, reach `awaiting` with an `approval` sub-reason when it declares `approval_needed`, and produce an inferred reading plus a watchdog recovery when it declares a `pty` source (`test_status_matrix_is_covered_per_harness_not_just_corpus_wide`).
  The corpus-wide matrix is a union that Claude's fixtures largely satisfy alone, so per-harness coverage is asserted separately.
- Every hooked harness declares `native_hooks`, its `hook_events`, a hook installer, and replay hook-step coverage.
- Every transcript-capable harness declares its transcript semantics and measurement parser, and declares `headless_probes.read_only` so the live conformance canary can drive its real CLI.
  A probe it cannot offer is declared `None`, which excludes it from that tier by derivation rather than by a skip inside the test: pi ships no subagent tool, so it declares no subagent probe.
- Every harness declares `conversation_discovery`: how mux finds conversations it wrote outside mux, either a filesystem layout under its data home or a store query.
  `None` is a permitted answer and states that mux does not index its past conversations; what is not permitted is leaving the question unanswered, which is how three harnesses came to be silently undiscoverable.
- Every harness declares automation evidence and tool-catalog provenance.
- Add the harness name to the closed `Backend` literal and handle every new `assert_never` failure explicitly.
- Add provider-specific branches only for real differences in record schema, auth, argv, resume, or TUI behavior, and only in a module `tests/test_harness_name_literals.py` allow-lists with the exhaustive dispatch that anchors it.
- Regenerate the browser seed (`packaging/generate_frontend_registry.py`) and verify the public registry payload and the launchable-harness frontend contract before enabling richer levels.

## What CI enforces for a harness

Parity is enforced by tests that derive their requirements from the descriptor, so a capability a harness genuinely lacks costs nothing while an undeclared gap fails.
Exempting a harness means changing its descriptor, never weakening a test.

| Tier | Question | Where |
|---|---|---|
| Declaration | Is the descriptor complete and self-consistent? | `tests/test_harness_registry.py` |
| Contract | Do the daemon and the browser hold one registry? | `test_generated_frontend_registry_seed_is_current`, `test_every_browser_read_trait_travels_in_the_public_payload` |
| Wiring | Does every consumer derive behaviour rather than name a harness? | `tests/test_harness_name_literals.py`, plus `assert_never` in every dispatch |
| Coverage | Is every declared capability actually reachable for this harness? | `test_conversation_discovery.py`, `test_opencode_transcript.py`, `test_harness_registry.py` |
| Behaviour | Does the harness exercise the status surface it declares? | `tests/test_detection_replay.py` (per-harness matrix and corpus floors) |
| Live (transcript) | Does the real CLI still behave as the observer expects? | `tests/test_live_agent_conformance.py`, marker-gated; `test_every_transcript_harness_is_covered_by_the_live_canary` runs without credentials and fails when a transcript harness has no live coverage |
| Live (store) | Does a store-backed CLI's exact measurement still read? | `tests/test_live_agent_conformance.py` store canary, marker-gated; opencode runs `opencode run` into an isolated store and asserts `session_measurements` |
| Live (automations) | Does a real run's facts drive the detectors and memory tools? | `tests/test_live_automations.py`, marker-gated; `test_every_fact_producing_harness_is_covered_by_the_automations_canary` runs without credentials |
| Live (MCP wire) | Does a real session obey the control tools through `/mcp`? | `tests/test_live_mcp_control.py`, marker-gated; `test_every_mcp_capable_agent_harness_is_covered_by_the_control_canary` runs without credentials |

The live tiers are excluded from `.worktree-verify` and CI because they need an authenticated CLI and consume quota.
Each is derived from a declared capability rather than a per-harness skip, so a new harness either joins its tier or states on its descriptor why it cannot, and the coverage guard fails until it does.
The four derivations partition the harnesses.
A transcript-file harness (`transcript_dialect` set and `reports_transcript_path` true) is driven by the transcript canary; a store-backed harness (`measurement_source == "database"`, so opencode) is driven by the store canary instead, because it writes no file to replay.
A transcript harness that also declares an `automations` probe (one that permits a write, a command, and a test) is driven by the automations canary, which replays its real facts through the deterministic detectors and the mux memory tools in process with no daemon and no port.
An agent harness with an MCP client (`adapter_family != "pi"`) is driven by the MCP wire canary, which stands up an isolated daemon on an ephemeral port and drives its real session through `/mcp`; pi is excluded there by its declared no-MCP-client capability.
Run the tiers deliberately with `SWEMUX_RUN_LIVE_AGENT_TESTS=1` (transcript and store), `SWEMUX_RUN_LIVE_SUBAGENT_TESTS=1`, `SWEMUX_RUN_LIVE_PHASE2_TESTS=1`, `SWEMUX_RUN_LIVE_AUTOMATIONS_TESTS=1`, or `SWEMUX_RUN_LIVE_MCP_TESTS=1`.

## Operations

- Claude and Codex continue to use their ordinary shared home directories. Provider account
  selection swaps only the system auth file, so adapters, shims, configuration, skills,
  transcript discovery, and live-process behavior require no account-specific launch path.
- Claude explicit spawn uses `--session-id`; resume uses `--resume`.
- **A Claude pane's hook credentials travel with its `--settings` file, not its environment.**
  Each pane gets `<data_dir>/sessions/<mux id>/claude-hooks.json` whose hook commands carry
  `--identity <data_dir>/sessions/<mux id>/hook-identity.json`, and that file holds the pane's
  ingress URL, hook secret, and spool path. `hook_client` prefers it and falls back to
  `MUX_HOOK_URL`/`MUX_HOOK_SECRET`/`MUX_HOOK_SPOOL` for harnesses with no per-session settings
  file.
  The environment alone is not sufficient because Claude 2.1.227 can hand a pane's conversation
  to a background job run by a shared, long-lived `claude daemon run` process. That daemon is
  started once by whichever CLI first needs it and every background agent it later spawns
  inherits *that* process's environment, while `--settings`, `--mcp-config`, and `--add-dir` are
  passed per request and name the requesting pane. Measured 2026-08-10: one such daemon held a
  pane that had exited at 20:19:37 and posted 744 hook events to `/api/hooks/<retired id>`, every
  one HTTP 404, while the pane the work belonged to received a single `SessionStart` in its
  lifetime and its spool accumulated under the retired pane's key where nothing drains it.
  The identity file is rewritten on every spawn so it can never hold a superseded secret, and
  `session_env` only materializes a bare settings file when none exists — rewriting the pane's
  own would strip `--identity` back out of it.
- Worktree startup policy belongs to adapters because trust artifacts and primary-checkout access differ by harness.
  The base adapter has no-op `preflight_worktree` and `worktree_spawn_args` hooks, so future harnesses opt in without provider branches in worktree orchestration.
  Claude preflight clones the canonical primary trust entry into `~/.claude.json`, carries `.claude/settings.local.json`, and contributes `--add-dir`.
  Codex preflight updates the `config.toml` under the adapter's resolved data home, including an externally supplied `CODEX_HOME`, and contributes a sandbox writable-root override.
  OMP has no known startup trust gate and retains the no-op behavior.
  Every preflight is best effort because an interactive harness trust prompt remains the fallback.
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
- When mux MCP is registered, the same generated Claude settings allow only the closed read-tool set without a permission prompt.
  `notify` and `request_spawn` are deliberately absent from that allowlist and retain Claude's normal tool approval.
- **`hook_approval_decisions`** declares whether a harness lets a hook *answer* a permission
  request rather than only observe one — whether it reads a decision back off the hook command's
  stdout. Claude does (`PermissionRequest` fires only when a prompt would be shown, carries
  `tool_name`/`tool_input`, and takes `hookSpecificOutput.decision`); the rest publish their
  permission events on a one-way bus, and opencode's decision-capable plugin hook is not the one
  the mux plugin subscribes to. Declared rather than inferred because the failure is silent in
  the worst direction: a harness wrongly marked capable renders an approval-mode selector that
  changes nothing while the operator believes requests are being answered. Only a harness
  declaring it receives the explicit `timeout` on its decision hook, and only it can hold a
  non-`wait` approval mode (`approvals.md`).
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
  Each `agent_start` also mints a root `turn_id` that its `agent_end` echoes.
  The terminal payload derives `outcome` from the final assistant message in `agent_end.messages`, because OMP 17.2.10 clears its transient abort flag before extension subscribers run while retaining `stopReason: "aborted"` in that message list.
  An aborted root closes as interrupted even when `willContinue` is true; continuation describes the next agent run and may never reopen a root turn that another source already closed.
  Source launches copy the checked-out asset, while a frozen desktop rebuild copies the entire
  `swe_mux/assets` directory into the bundle, so the standard redeploy flow refreshes the source
  used by newly materialized packages.
  Existing PTYs retain their original argv and require a fresh omp process to load a changed
  extension.
  Mux sets `TERM_SESSION_ID=swe-mux-<mux-id>` for each OMP process (its `session_env`).
  The xterm-compatible capability environment and emulator/multiplexer marker shadowing that
  keep a CLI from inheriting the daemon's launch context are not OMP-specific: every session
  gets them centrally (`spawn_contract.terminal_env`; see `sessions.md`), and agent harnesses
  also get colour forced past their TTY-hiding launch chain. OMP's adapter therefore contributes
  only its own `PI_*` tuning (`OmpAdapter._omp_env`): DEC 2026 synchronized output is disabled
  because a retained byte replay can cross a paint transaction boundary, and native image
  protocols are disabled because mux exposes OMP's text fallback rather than an inline-image
  terminal addon.
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
  OMP deliberately declares no provider-account management.
  Historical ccusage source discovery is independent of this descriptor.
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
- **pi is an oh-my-pi family member, not an oh-my-pi clone.**
  oh-my-pi forked upstream pi, so the session file is the same shape: a `{"type":"session","version":3,...}` header, an `id`/`parentId` entry tree with a mutable leaf, and the same `usage` (`input`/`output`/`cacheRead`/`cacheWrite`) plus `usage.cost.total` on an assistant message.
  pi therefore reuses the omp record classifier rather than a near-copy of it, and `_omp_context_window` already resolves the window from the session's own adapter.
  Three measured divergences (pi 0.74.2, 2026-08-10) are why the adapter is separate.
  - **Session bucket encoding.** pi always writes `sessions/--<absolute path with `/`, `\`, and `:` collapsed to `-`>--/<timestamp>_<uuid>.jsonl`, including under the user's home directory. omp shortens a home-relative or temp-relative path to a scope-prefixed short name, so reusing its encoder finds nothing for the common case and fails silently rather than raising. `adapters/pi.py:pi_session_dir_name` owns pi's rule and is pinned by test.
  - **No terminal breadcrumb.** pi's data home holds only `auth.json`, `extensions/`, and `sessions/`; upstream `pi-tui` ships no `ttyid.ts` and pi's own documentation states session identity serves that purpose. Binding is therefore hook-first: the mux extension reports `ctx.sessionManager.getSessionFile()` and `getSessionId()` on every envelope, which is why the descriptor sets `reports_transcript_path`. Cwd-bucket correlation is the fallback when the extension does not load.
  - **`parentSession` rather than `previousSessionFiles`.** pi records a single forked-from path; omp keeps an append-only array. Only omp's can describe a chain.
- **pi has no approval flow, so its descriptor does not claim one.**
  pi runs a tool as soon as the model asks for it; gating is something a user extension implements over `tool_call` (pi ships `examples/extensions/permission-gate.ts` doing exactly that).
  Its `normalized_events` therefore omits `approval_needed`, which keeps the replay corpus from demanding a fixture for evidence pi cannot produce.
  Its sibling extension independently applies the same root-ID and final-assistant-outcome contract as OMP, because upstream pi's `agent_end.messages` is also the durable abort source.
- **pi has no MCP client**, so the mux MCP server is not registered for it and its MCP configuration table is empty rather than searched.
  Exposing mux's tools to pi would mean registering them as extension-provided native tools, which is a different mechanism and is not done.
- **opencode does have MCP, and mux registers its server in the same per-session config layer that carries the plugin** — `type: remote`, the mux Streamable HTTP url, and an `Authorization` bearer header.
  The token is written literally into a session-private `chmod 600` file, as omp's extension package carries one: mux mints a distinct token per session, so a shared registration would either fail authentication or hand one session's identity to another.
  opencode's `{env:VAR}` interpolation is deliberately unused for exactly that reason, since it resolves from whatever environment the process happens to carry.
  A url without a token writes no `mcp` block at all, because half a registration authenticates on nothing.
  Verified live 2026-08-10: an opencode agent asked to list its `mux` tools returned all six.
- **Every per-session artifact root is exported to the shim environment**, not only the adapter path.
  `MUX_OPENCODE_CONFIG_ROOT` was once read by the launcher and never exported, so an opencode started by typing `opencode` in a shell pane materialized no config — no plugin, no hooks, no state, no MCP — while the same harness from the Run menu worked.
  A divergence between the two launch paths is the hardest kind to notice, because the path under test is rarely the path in use.
  Extension-registered native tools are the route if that surface is ever wanted.
- **`PI_CODING_AGENT_DIR` is shared between pi and oh-my-pi and is read by both descriptors.**
  Mirroring the CLI's own resolution is the job of a data-home resolver, so an exported value moves both harnesses exactly as it moves both CLIs.
  Overlap is survivable because a pi conversation binds from its extension's reported session file and a live transcript may only be claimed by one session.
  `PI_CONFIG_DIR` is an oh-my-pi addition and is deliberately not read for pi.
  Redirecting either harness to a mux-private agent directory is rejected: credentials, sessions, and the agent database live there, and per-pane redirection strands them.
- **opencode keeps conversations in SQLite, so it has no transcript to tail — but it does have one to read.**
  `~/.local/share/opencode/opencode.db` holds `session`, `message`, `part`, `permission`, and an ordered `event(aggregate_id, seq, type, data)` log keyed by session id.
  The store location follows the XDG base-directory spec on every platform: `_opencode_data_home` honours `XDG_DATA_HOME` (writing to `<XDG_DATA_HOME>/opencode`) so mux reads the database where the CLI actually wrote it, because a plain `~/.local/share/opencode` guess silently mis-locates it whenever XDG is redirected; `OPENCODE_DATA_DIR` stays the highest-precedence explicit override.
  The byte-offset transcript *tailer* has no referent here, so `transcript` is absent from `state_sources` and every path-returning method on the adapter answers `None`.
  Reading the conversation is a separate question, and the answer is `opencode_store`: it projects `message` and `part` rows into the same record stream the file-backed dialects produce, and the descriptor declares `transcript_dialect="opencode"` with `conversation_store_file="opencode.db"`.
  Every reader above that boundary (Transcript tab, copy-reply, read-aloud, history indexing and search, MCP `read_transcript`, observer slices, tool telemetry) is unchanged; they take a conversation reference of `(path | None, backend, native_id)` instead of a path, and `conversation_is_readable` replaced every `path.is_file()` gate.
  Selecting by session id is also what scopes a read to the root conversation: a subagent runs in its own `session` row carrying `parent_id`, so it is excluded by the `WHERE` clause rather than by a filter, which is the rule Claude enforces by hand with `isSidechain`.
  Its watermark is opencode's own `(time_updated, message_count)` and never a file stat: the database file's mtime describes every conversation at once, and Windows freezes an open file's mtime at creation, so neither could tell whether *this* conversation changed.
  Both halves are needed — `time_updated` alone misses a second message written inside the same millisecond, and the count alone misses a row edit, which is what a streamed assistant message completing is.
  Measurement needs no tailer either: opencode maintains running `cost` and `tokens_*` totals on the `session` row itself, so `measurement_source="database"` reads them with one indexed lookup at the turn boundary.
  No parse, no byte offsets, and no exposure to the Windows frozen-mtime hazard, because nothing here derives freshness from a timestamp.
  That is why opencode reaches `managed` without a transcript *file*: the tier derives from the two capability axes, and neither of them is "has a file".
  Retroactive discovery works for it too, by querying `session` rows rather than walking a tree (`discover_store_conversations`), and returns records with no `path` because the conversation id is the whole address.
  Root rows only: a subagent carries `parent_id`, which is the same rule Claude applies with `isSidechain` and Codex with `parent_thread_id`.
  The database is opened read-only against the live WAL, which neither blocks nor corrupts opencode's writer.
  `immutable` is deliberately not set — it would pin the snapshot and hide every update the session is still making.
  A missing row publishes nothing rather than zeroes, because a published zero is indistinguishable from a genuinely empty conversation.
  The `event(aggregate_id, seq, type, data)` table remains available as an ordered per-session log if state ever needs a second source; state currently comes from the plugin's hooks alone.
- **Each harness's context window comes from its own catalogue, never a neighbour's.**
  opencode caches the models.dev data at `~/.cache/opencode/models.json` (`limit.context`); pi ships its own under `@earendil-works/pi-ai`.
  They disagree on the same model: measured 2026-08-10, `openai/gpt-5.6-sol` is 1,050,000 tokens to opencode and 272,000 to pi.
  Each harness routes through its own provider stack, so its own view is the correct one for its sessions, and borrowing the other's would render context percentage wrong by roughly 4x.
  An unknown model reports no context at all rather than 0%, which renders as a fresh conversation rather than a missing reading.
- **opencode's plugin is added through `OPENCODE_CONFIG`, never `OPENCODE_CONFIG_DIR`.**
  opencode merges its configuration layers, and `OPENCODE_CONFIG` names one additional file, so a mux-owned JSON listing the plugin by absolute path adds the plugin while the user's global, project, and `.opencode/` layers keep loading.
  `OPENCODE_CONFIG_DIR` *replaces* the config directory, which would require mirroring the user's agents, commands, modes, plugins, and auth into an overlay and keeping that mirror honest — on Windows through directory junctions, whose teardown is a known hazard.
  Verified against opencode 1.18.16 (2026-08-10): a plugin named by absolute path in an `OPENCODE_CONFIG` file loads and receives the full event bus.
  A failure to materialize the config forfeits the plugin, never the pane.
- **opencode reports both halves of an approval** (`permission.updated` raises, `permission.replied` resolves), which is the discriminator Codex still lacks.
  Its one unambiguous root-completion signal is `session.idle`; `session.status` carries `busy`/`idle`/`retry`.
  The plugin assigns one synthetic root ID on the first `busy` status, reuses it across duplicate busy reports, and echoes it from `session.idle` or `session.error`.
  Abort, cancel, and interrupt-shaped error names normalize to `interrupted`; other errors normalize to `error`.
  Its live bus emits more types than the SDK's typed union (measured: `message.part.delta`, `plugin.added`, `catalog.updated`, `reference.updated`, `integration.updated`), so the plugin classifies known types and drops the rest rather than guessing.
- **A first turn on a self-minting harness reads as historical, and that is not a fault.**
  A harness that mints its own conversation id cannot be bound until it reports one, and by then it has usually written its header and often the whole first turn.
  Those records dispatch through the catch-up path, which publishes measurements but deliberately emits no events and does not count toward the parser statistics.
  So a freshly spawned pi pane reports `parser_status: watching` with `parser_events_seen: 0` while already showing correct tokens, cost, and model — measured 2026-08-10: turn one `watching`/0 with 2804 tokens, turn two `ready`/2 with 3064.
  Schema-drift detection is therefore live from the second turn onward, not the first.
  Reading the first turn alone makes a working session look broken, which is why it is written down rather than rediscovered.
- **Neither pi nor opencode declares `pty`.**
  The PTY rule table is backend-scoped and each harness's markers must be pinned to captured screens from the installed build; neither has been captured.
  A screen rule inferred from documentation is the marker-drift problem restated.
  opencode additionally holds the alternate screen, where a tail rule reads a repainted frame rather than a transcript.
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
  refuses such a profile outright. The detected cmd.exe profile currently uses `/Q` without
  `/D`, so registry AutoRun commands can still mutate PATH or command precedence before the
  prompt; it carries no equivalent guard. WSL cannot use Windows `.cmd` shims at all, so a
  WSL profile is `agent-bridge-unavailable` unless the distro-side bridge is installed and
  reachable (`wsl_bridge.py`, opt-in through `wsl_bridge_enabled`).
- **Shims are written in the host's executable-script format**: `.cmd` on Windows, a
  `#!/bin/sh` script that `exec`s the launcher on POSIX. The POSIX shim is deliberately
  extensionless, because `claude` is what the user types and what harness detection looks up
  - which is also why `is_mux_shim` gates on a per-host suffix rule. Accepting only
  `.cmd`/`.bat` there would make every POSIX shim read as a real CLI, so detection would
  report every harness installed and every launch would recurse into the shim.
- **A POSIX host must not launch a Windows agent, and under WSL it easily can.** The Windows
  install is on PATH through interop, so `which("claude.exe")` succeeds and resolves under
  `/mnt/`. Such an agent runs, and is wrong in every way that matters: it reports the
  wsl.localhost share as its working directory, writes its transcript into the Windows home
  where no Linux path points, and joins no Linux process group, so cleanup cannot reach it.
  Two rules prevent it - `harness.host_executable` drops the `.exe` off Windows, and
  `which_real` refuses any resolution `host_platform.is_windows_interop_path` recognizes.
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
- **An in-CLI conversation replacement is a new agent run, not a retarget.** `/clear` (Claude),
  `/new` (Codex), and a Claude pane whose conversation is parked into a background job
  mint a new native session id and a new transcript file under the same PTY,
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
  is unaffected by sibling sessions. It is one of Claude's *two* rollover paths, and both are
  the CLI's own report of where it is now writing: the other is the `parkedJobId` its
  per-process state file publishes when a pane's conversation is parked into a background job
  no hook can speak for (`status-detection.md`). Adapters declare
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
- **"Dead" is measured by observed transcript evidence, never by the file's timestamp alone** (`_transcript_last_write_ts`).
  Windows does not keep a live file's last-write time current: measured 2026-08-06, every long-running Codex rollout on the machine reported an mtime frozen at the file's *creation*, 290 s to 3.5 h behind content that had grown to 5 MB, with every Win32 timestamp API returning that same frozen value.
  `st_size` stayed accurate, and Claude transcripts were unaffected in the same survey.
  The daemon dates live writes from its own tailer, which polls the size every 250 ms and stamps `Session.transcript_growth_ts` when bytes appear past its attach snapshot.
  It also retains `Session.transcript_record_ts`, the newest valid provider timestamp carried by a record in the followed file.
  The record timestamp closes the late-bind race where the first observer attaches after a complete turn is already present and therefore sees no post-attach growth.
  Replaying old bytes does not make the record timestamp fresh, so a new turn hook against an abandoned file still trips staleness.
  Trusting mtime alone made this guard fire on healthy Codex sessions roughly 90 s into their life, which is a **false-safe inversion**: the operator sees `idle · turn complete` everywhere while the prompt queue refuses to deliver and learns to click through the confirmation meant to stop them.
- Growth is tracked separately from record reads because it covers what reads cannot: a partial
  line, or one the parser rejects, is still proof the file is alive, and
  `_record_parser_observation` never fires for it. Bytes already present when the tailer
  attached are replay and are deliberately *not* growth — counting them would suppress
  staleness detection after every daemon restart, on exactly the sessions this guard exists
  for. The stamp describes one file and is discarded when the observer is re-aimed at another
  (`_aim_observer`), but **not** when it re-tails the same file after a fault.
- Retraction is evidence-based, not the negation of the staleness predicate.
  An inferred `transcript_stale` or `transcript_missing` claim is dropped when the followed transcript is written after the mark, or when a timestamped catch-up record corroborates the latest transcript-backed turn hook within the quiet window.
  A rollover refused because a live sibling owns the conversation and a CLI-reported rollover that could not be adopted are explicit mismatch claims.
  Those claims are never cleared by quiet or replayed content from the file the CLI abandoned.
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
- The Claude context window comes from `claude_models.claude_context_window`, one table
  shared by the live observer and the history reconciler. Unlisted models resolve by
  model *family*, longest prefix first. That fallback is load-bearing: a bare table
  lookup made an unrecognized model report a zero-token window, which renders as 0%
  context used — indistinguishable from a fresh conversation rather than obviously
  broken, and every session on a newly released model looked idle-fresh forever.
- The Codex model is read from `turn_context.payload.model`, per turn. `session_meta`
  carried it in an earlier CLI and no longer does, and `token_count`'s `info.model` is
  absent too, so a parser reading only those reported no model for any Codex session.
  Reading it per turn also picks up a mid-conversation model switch.
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
- Record readers: `src/swe_mux/transcript_view.py` (dialect dispatch, conversation reference), `src/swe_mux/opencode_store.py` (store-backed records)
- Generated browser seed: `frontend/src/harnessRegistrySeed.ts`, written by `packaging/generate_frontend_registry.py`
- Browser registry reader: `frontend/src/harnessRegistry.ts`
- Parity enforcement: `tests/test_harness_registry.py`, `tests/test_harness_name_literals.py`, `tests/test_detection_replay.py`, `tests/test_live_agent_conformance.py`
- Adapters: `src/swe_mux/adapters/` (pi: `adapters/pi.py`; opencode: `adapters/opencode.py`)
- Injected lifecycle reporters: `src/swe_mux/assets/omp_mux_hook.ts`, `src/swe_mux/assets/pi_mux_hook.ts`, `src/swe_mux/assets/opencode_mux_plugin.js`
- Tailer/parsers: `src/swe_mux/observation.py`
- Hook command: `src/swe_mux/hook_client.py`
- Unicode boundary: `src/swe_mux/text_safety.py`
- CLI shims: `src/swe_mux/launchers.py`, `src/swe_mux/agent_launcher.py`
- Promotion lifecycle: `src/swe_mux/session.py`
- Replay/readiness contract: `delivery-readiness.md`
