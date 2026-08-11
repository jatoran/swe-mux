# Harness parity audit, 2026-08-11

**Status: closed.**
Every finding below was addressed in the same change; the audit is retained as the record of what the gaps were and why the enforcement has the shape it does.
The resulting contract is documented in `design/features/backends.md` (§ Registry contract, § Supporting a new harness, § What CI enforces for a harness).
Outcomes are recorded per section.

Classification of every place in swe-mux where adding a harness costs work, and of how each place behaves when a new harness arrives without that work being done.

The question this answers: which surfaces already force a new harness to be handled, which surfaces fail loudly when it is not, and which surfaces silently produce a plausible wrong answer.
Only the third class needs new machine enforcement.

Method: every occurrence of a harness name literal in `src/swe_mux` (261 across 23 files) and in `frontend/src` (27 across 10 files) was read in context and classified, then cross-checked against `HarnessDescriptor` fields, the `assert_never` anchors, and the replay corpus.
Findings marked *verified* were read in the source at this commit.

## Classification scheme

Each surface falls into exactly one class.

| Class | Behaviour when a new harness is not handled | Enforcement needed |
|---|---|---|
| **A. Registry-derived** | The harness is handled correctly with no code change | None |
| **B. Anchored dispatch** | `mypy` fails on `assert_never`; the harness cannot ship unhandled | None |
| **C. Unanchored name branch** | The harness silently takes a default written for someone else | **This is the gap** |
| **D. Duplicated across the language boundary** | Two copies drift; the wrong one is usually the TypeScript one | **This is the gap** |

Class B is the pattern the codebase already prefers, and it is why most of the 261 literals are not a risk.
`agent_environment._policy_inventory` naming five harnesses in an `if/elif` chain is not a parity hazard, because the chain ends in `assert_never(backend)` and a sixth harness is a type error at the point the descriptor is added.

## Class A: registry-derived surfaces

These cost nothing for a new harness beyond its descriptor.
They are listed so that future work does not mistakenly add a name branch to one of them.

| Surface | Mechanism |
|---|---|
| Shim generation (`launchers.create_agent_shims`) | iterates `agent_harnesses()`; asserted by `test_agent_shims_cover_the_registry_and_keep_the_content_guard` |
| Adapter construction (`adapters.build_agent_adapter`) | dispatches on `adapter_family`, anchored |
| History rows, search, agent visibility (`history.py`) | gated on `AGENT_BACKENDS` / `has_observable_transcript` |
| Prompt queue, delivery readiness, auto-delivery | gated on `HarnessLevel` and `delivers_prompts_through_pty` |
| Voice target eligibility (`voice.py`) | gated on `has_observable_transcript` |
| Usage manager provider set (`usage.py`) | derived from `external_usage_command` |
| Provider account provider set (`provider_accounts.PROVIDERS`) | derived from `provider_account_management` |
| Public registry projection (`public_harness_registry`) | iterates `HARNESSES` |
| Resize repaint pulse (`needs_resize_repaint`) | derived from `screen` |
| Frontend capability gates (`harnessRegistry.ts` accessors) | read the installed payload, no names |

## Class B: anchored dispatch sites

Twenty-one dispatch sites terminate in `assert_never`.
A new harness added to the `Backend` literal is a `mypy` error at every one of them, which is the intended cost and the intended failure mode.

| Module | Sites | Dispatch key |
|---|---|---|
| `agent_environment.py` | 7 (`:566`, `:712`, `:957`, `:1180`, `:1242`, `:1283`, `:1319`) | `Backend` |
| `transcript_view.py` | 3 (`:84`, `:231`, `:393`) | `TranscriptDialect` |
| `observation.py` | 3 (`:792`, `:980`, `:1042`) | `Backend` |
| `agent_skills.py` | 2 (`:691`, `:709`) | `Backend` |
| `agent_launcher.py` | 2 (`:305`, `:321`) | `Backend`, `AdapterFamily` |
| `cli_state.py` | 1 (`:78`) | `Backend` |
| `reconcile.py` | 1 (`:301`) | `Backend` |
| `operational_telemetry.py` | 1 (`:2228`) | `Backend` |
| `adapters/__init__.py` | 1 (`:77`) | `AdapterFamily` |

`cli_state._publishes_cli_state` is the model form: one exhaustive predicate, one measured sentence per harness explaining its answer, `assert_never` at the end.

Two declared-table variants belong to the same class in spirit, because a missing entry means "no behaviour" rather than "wrong behaviour", and both say so in place:

- `session.SCREEN_RULES` scopes each PTY rule with an explicit harness set (`frozenset({"claude"})`, `frozenset({"pi"})`).
- `terminalCaretPlacement.CARET_RESOLVERS` maps a harness to its caret resolver; an absent harness gets no tap steering.

## Class C: unanchored name branches

Each of these takes a harness name and applies special behaviour, with an implicit `else` that a new harness falls into silently.
The column *verdict* states whether the branch is genuinely harness-unique (keep the branch, declare the exemption) or is a capability question that belongs on the descriptor.

### Identity and conversation binding

| Site | Behaviour | Verdict |
|---|---|---|
| `session.py:2750`, `:2757` | Spawn-argv native-id capture recognizes only `--session-id`/`--resume` (Claude) and `resume` (Codex) | **Descriptor**: the resume-argv shape is already adapter knowledge (`resume_spec`); this re-derives it from strings |
| `session.py:3034`, `:3042` | Claude-only standing claim on the pane's own minted conversation id | Genuinely unique, follows from `assigns_conversation_id`; **should test that predicate, not the name** |
| `session.py:3991` | `if confirmed or backend != "claude"` gates lifecycle-anchor rewriting | **Descriptor**: this is "may a guess rewrite the anchor", a capability question |
| `session.py:4115` | Mismatch warning logged only for Claude | Diagnostic only; a new harness loses the log line silently |
| `session.py:4895`, `:4939`, `:4965`, `:4973` | Conversation-ownership healing is Claude-only | Genuinely unique (depends on mux minting the id), **derivable from `assigns_conversation_id`** |
| `session.py:4997` | Anchor candidates extended only for Claude spawns | Same predicate as `:3991` |
| `spawn_contract.infer_agent_executable_backend` | Recognizes only `codex*`/`claude*` executables and their npm entrypoints | **Descriptor**: `executable` and `script_base_name` are already declared per harness |

`infer_agent_executable_backend` feeds `history.py:951`, the repair that detects a history row whose claimed backend disagrees with the executable actually launched.
For omp, pi, and opencode it returns `None`, so that repair never runs.
It fails safe, which is why nothing has noticed.

### Status detection

| Site | Behaviour | Verdict |
|---|---|---|
| `session.py:1180`, `:1182` | `backend in {None, "claude"}` gates whether a CLI-state reading may veto or override the screen reading | **Duplicate of an existing anchored predicate.** `cli_state._publishes_cli_state` answers exactly this question, exhaustively and with per-harness reasoning, and this call site does not use it |

This is the clearest single instance of the failure mode the audit was looking for: the correct, exhaustive, documented predicate exists in the codebase and a hardcoded copy of its Claude answer sits in the consumer.

### Measurement and telemetry

| Site | Behaviour | Verdict |
|---|---|---|
| `operational_telemetry.TOOL_PARSER_VERSIONS` | Declares parser revisions for claude, codex, omp only; lookups fall back to `f"{backend}-phase2-v1"` | **Silent default.** pi shares omp's `phase2-v2` parser but is versioned `pi-phase2-v1`, so a future omp parser revision will not invalidate pi's cached coverage rows |
| `provider_accounts.py` (16 literals) | Credential paths, auth-file names, identity extraction, and the login argv are all `claude`-or-`codex` shaped | Genuinely unique per provider, but the module has **no exhaustiveness anchor**, so a third managed-account harness would silently reach Codex-shaped code |

### Agent context and environment

| Site | Behaviour | Verdict |
|---|---|---|
| `agent_context.INSTRUCTION_SOURCES`, `GLOBAL_INSTRUCTION_SOURCES` | Only `claude`/`CLAUDE.md` and `codex`/`AGENTS.md` | **Descriptor**: the instruction filename is a per-harness fact of the same kind as `config_dir_name` |
| `agent_context._claude_provider`, `_codex_provider` | Learned-memory providers exist only for those two | Legitimately absent elsewhere, but absence is undeclared, so the drawer shows nothing without saying why |
| `agent_environment.py:1434` | omp's extension hook items are prepended additively, outside any anchored chain | Genuinely unique; low risk because it is additive |

### Server behaviour

| Site | Behaviour | Verdict |
|---|---|---|
| `server.py:2441` | Second-opinion review flips between exactly two backends | **Descriptor or registry query**: with five harnesses "the other supported agent" is no longer well defined |
| `server.py:3394` | Spawn from an observation defaults to `"claude"` | Reasonable default, undeclared |
| `server.py:4602` `_BRANCH_STRATEGY_BACKENDS` | Branch is refused outside claude/codex, and the refusal is explicit (`branch_unsupported`) | Correct as written; **duplicated in the frontend** (Class D) |
| `server.py:952`, `:953` | Hook settings and MCP config paths are read off `adapters["claude"]` for the shared child environment | Structural coupling to Claude's adapter; not a per-harness branch but worth recording |
| `server.py:332` | Late OSC 10/11 color replies filtered for Codex only | Genuinely unique, measured |

## Class D: knowledge duplicated across the language boundary

The backend holds the measured fact; the frontend holds a hand-written copy.
Three copies have already drifted.

### D1. The registry seed is stale (verified)

`frontend/src/harnessRegistry.ts:44-50` seeds the pre-snapshot registry by hand.

- opencode is seeded `level: 'hooked'`, `measurement_source: 'none'`, `capabilities.measurement: false`.
  Its descriptor is `managed` with `measurement_source="database"`, because its `session` row carries running token and cost totals.
  Until the first daemon snapshot installs, opencode panes render with measurement surfaces suppressed.
- pi is seeded `state_sources: ['hook', 'transcript']`; its descriptor declares `("hook", "transcript", "pty")`.
- The TypeScript type is `measurement_source: 'transcript' | 'none'`, which cannot express `'database'`.
  `installHarnessRegistry` spreads the payload without validating it, so the real value lands in a field whose type says it is impossible and `tsc` never sees the mismatch.

A fourth copy lives in `frontend/test/harnessRegistry.test.ts:17-24`, carrying three harnesses and no `repaints_scrollback`.

Nothing compares any copy to `public_harness_registry()`.

### D2. The skill invocation prefix disagrees (verified)

`agent_skills.py:263-268` is the measured authority: `/` for claude, pi, and opencode; `/skill:` for omp; `$` for codex.
It ships that string to the client as `DiscoveredSkill.invocation`, and `CommandsTab.tsx:142` injects it verbatim, which is correct for every harness.

`commandRail.railPayload` (`commandRail.ts:521`) re-derives the prefix in TypeScript as `backend === 'codex' ? '$name' : '/name'`, with no omp case.
A rail skill item authored as a bare name (which is what `RailEditor.tsx:579` prompts for) therefore types `/name` on an omp pane where the CLI expects `/skill:name`.

### D3. Branch capability is stated twice

`server.py:4602` defines `_BRANCH_STRATEGY_BACKENDS = frozenset({"claude", "codex"})` and refuses anything else server-side.
`TerminalPane.tsx:3197` independently computes `branchable = backend === 'claude' || backend === 'codex'` to decide whether the button is offered.
The server refusal is the safety net, so drift here degrades to a button that fails rather than to a wrong action, but the fact is still authored twice.

Two further duplications are lower risk and listed for completeness: `ProviderAccounts.tsx:241` hardcodes the login command strings that `provider_accounts.py:1503` also builds, and `resumeCommand.ts:29-31` re-states the resume argv that each adapter's `resume_spec` already knows.

## Undeclared capabilities

Behaviour that is real, measured, and load-bearing, but has no descriptor field, so a new harness cannot express it and inherits someone else's answer.

| Capability | Where it currently lives | Why a field is warranted |
|---|---|---|
| WebGL renderer is unsafe for this harness | `terminalRenderer.ts:39`, hardcoded `backend === 'claude' \|\| backend === 'omp'` | The docstring states this is a *different* failure from `repaints_scrollback`: a retained pane's live WebGL context is intermittently mangled with no context-loss event. A new alternate-screen harness with a retained surface silently gets WebGL |
| Explicit `webgl` override may be offered | same site (omp is excluded from the override, Claude is not) | Two distinct answers currently encoded in one expression |
| Terminal max-column clamp and app-scroll ownership | `terminalViewport.ts:70`, `:110`, `:207` (claude, codex) | TUI layout facts of the same kind as `screen` |
| Terminal protocol negotiation quirk | `terminalProtocol.ts:29` (codex) | Measured per-harness fact with no home |
| Root instruction filename | `agent_context.INSTRUCTION_SOURCES` (`CLAUDE.md`, `AGENTS.md`) | Same kind as `config_dir_name`, which is already declared |
| Skill invocation prefix | `agent_skills.py:263-268` | Already computed per harness; publishing it on the descriptor would let the rail stop guessing (fixes D2 structurally) |
| Resume argv shape | adapters plus `session.py:2750` plus `resumeCommand.ts` | Stated in three places, twice by string matching |

## Replay corpus coverage

Fixture counts at this commit: claude 37, omp 11, codex 7, pi 6, opencode 5.
Every harness clears its derived floor (`SCENARIO_FLOOR[managed] = 5`).

The per-harness assertions in `test_replay_corpus_covers_phase1_evidence_and_lifecycle_matrix` cover *step kinds* and *state sources* only.
The richer matrices are corpus-wide aggregates: `test_replay_corpus_covers_phase35_status_matrix` unions `states_seen`, `awaiting_reasons`, `proofs`, and `watchdog_actions` across all fixtures.

Consequence: claude's 37 fixtures satisfy those global sets by themselves.
A new harness can meet every current corpus assertion while never exercising a single awaiting sub-reason, watchdog recovery path, or proof class of its own.
Per-harness status-matrix coverage is the one corpus dimension not enforced.

## What this implied for enforcement, and what shipped

All six landed. The contract they produce is documented in
`design/features/backends.md`; this list records which finding each one answers.

1. **The frontend registry is generated** from `public_harness_registry()` into
   `frontend/src/harnessRegistrySeed.ts`, and the three hand-written copies are gone.
   Closes D1 and prevents its recurrence.
2. **The Class C entries marked *Descriptor* are descriptor predicates.**
   `publishes_cli_state`, `assigns_conversation_id`, `native_id_from_args`,
   `resume_command`, `branch_strategy`, `suppresses_late_color_response`,
   `harness_for_command`, and the derived instruction sources replaced the name
   branches. `session.py`'s identity helpers were renamed off the vendor
   (`_owns_minted_conversation`, `_heal_minted_identity`) because they were never
   about one vendor, and `_CLAUDE_NATIVE_ID` was deleted in favour of the declared
   `native_id_pattern`.
3. **Twelve undeclared capabilities are descriptor fields**, and the frontend-read
   ones travel in the payload. Closes D2 structurally.
4. **The name-literal lint is `tests/test_harness_name_literals.py`**, parsing rather
   than grepping so prose is never a finding, with a module allowlist whose
   `assert_never` claims are themselves checked.
5. **The status matrix is per-harness**
   (`test_status_matrix_is_covered_per_harness_not_just_corpus_wide`), gated on what
   each descriptor declares. It found the opencode `tool_result` gap immediately.
6. **`provider_accounts` is anchored** on a closed `ManagedProvider` literal with an
   `assert_never`-terminated `_provider_profile`, so a third managed-account harness
   is a test failure and then a type error rather than a Codex-shaped credential
   write.

## Verified divergences to fix regardless of the CI work

These are defects found during the audit, independent of any enforcement mechanism.
All are fixed.

- `harnessRegistry.ts:44-50`: opencode seeded two capability levels below its descriptor; pi missing its `pty` state source; `measurement_source` type cannot express `'database'`.
  **Fixed** by generating the seed (`frontend/src/harnessRegistrySeed.ts`) and widening the union; `test_generated_frontend_registry_seed_is_current` fails while the file is stale.
- `commandRail.ts:521`: omp skill rail items inject `/name` instead of `/skill:name`.
  **Fixed** by publishing `skill_invocation_prefix` and reading it in the rail.
- `session.py:1180`, `:1182`: hardcoded Claude answer where `cli_state._publishes_cli_state` is the declared predicate.
  **Fixed**; both now call `publishes_cli_state`, which reads the descriptor.
- `operational_telemetry.TOOL_PARSER_VERSIONS`: pi versioned `phase2-v1` while running omp's `phase2-v2` parser, so its cached coverage rows will not invalidate when that parser is revised.
  **Fixed** by keying revisions on `transcript_dialect`. omp's stored value changes from `omp-phase2-v2` to `pi-phase2-v2`, which reparses its coverage rows once.
- `spawn_contract.infer_agent_executable_backend`: the `history.py:951` backend-mismatch repair is inert for omp, pi, and opencode.
  **Fixed** by deriving recognition from the registry (`harness_for_command`).

## Implementation gaps closed for the newer harnesses

Found while making the enforcement derive from declarations. Each was a capability the
harness has and mux did not use, rather than a test that needed relaxing.

- **Agent Context listed instruction files for Claude and Codex only.**
  Measured 2026-08-11 against the installed CLIs: oh-my-pi, pi, and opencode all read a root `AGENTS.md`, and each keeps its user-level copy somewhere its config directory does not predict (`~/.agent`, `~/.pi/agent`, `~/.config/opencode`).
  A project-root instruction file is now one entry naming every harness that reads it, so a change to a shared `AGENTS.md` reports the four harnesses it reaches rather than one.
- **Copy-resume was unavailable for pi and opencode.**
  Both resume by id through `--session`; the frontend held a three-name list instead of the declared grammar. Resume commands are now composed from `cli_name` plus `resume_argv`.
- **opencode declared `tool_result` and never emitted one.**
  Its plugin sends `PostToolUse`, but the hook path only emitted `tool_use`, so a hooks-only harness reported tool starts and never their completions. The hook path now emits `tool_result` for a harness with no transcript source, which is the only place such a harness can report one.
- **Retroactive discovery existed for two harnesses out of five.**
  `reconcile.scan_external_transcripts` held a hardcoded `specs` tuple naming Claude and Codex, so conversations omp, pi, and opencode wrote outside mux were on disk, readable, and never reached History, with nothing reporting the gap.
  The layout is now declared per harness (`conversation_discovery`) and the scanner derives from it: omp and pi share the inspector their shared record dialect already implies, and opencode is discovered by querying `session` rows.
  Measured after the change on a real machine: claude 140, codex 98, omp 50, opencode 15, pi 14.
- **The name-literal lint was scoped per module, which hid that finding.**
  `reconcile.py` was allow-listed as "assert_never anchored", true of one dispatch in it and false of the discovery scanner in the same file.
  The allowlist is now per function, `assert_never` is detected per scope, and a renamed or deleted exempted function fails `test_every_allowlist_entry_still_points_at_real_code`.
  Rescoping surfaced two more duplications, both fixed: `agent_skills` recomputed the skill invocation prefix that `skill_invocation_prefix` already declares, and `HOOK_INSTALLER_FAMILIES` was a literal set instead of being derived from `native_hooks`.
- **opencode had no readable conversation at all.**
  Its messages are `message` and `part` rows in `opencode.db`, and every reader in mux took a file path, so it had no Transcript tab, no copy-reply, no read-aloud, no history search over its replies, no MCP `read_transcript`, no observer slices, and no historical tool telemetry. `opencode_store` now projects those rows into the same record stream the file-backed dialects produce, and the readers take a conversation reference instead of a path. Detail in `design/features/backends.md`.
- **The live conformance canary named two vendors.**
  omp and pi write parseable transcripts and have documented non-interactive modes (`-p`, `--no-tools`), so both now run the canary. Each harness declares its `headless_probes`; opencode is excluded from the transcript canary by derivation because it writes no transcript, and pi declares no subagent probe because it ships no subagent tool.
- **Shim-launched settings were written under one harness's name.**
  `create_agent_shims` published `MUX_CLAUDE_SETTINGS` specifically, while `agent_launcher` reads `MUX_<NAME>_SETTINGS`; a second harness in the Claude adapter family would have started with no hook settings. The shims now publish per-harness from whichever adapters expose the paths.
