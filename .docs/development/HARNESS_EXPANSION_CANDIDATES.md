# Harness expansion candidates

Research feeding `ROADMAP.md` Phase 12.
One parity study per agent CLI that is not yet in the registry: what it can give each declared
capability, which registry gates it clears, and what has to be measured against a real install
before a descriptor is written.

This document does not restate how a harness is added.
That contract is `../design/features/backends.md`, and it is the authority.
This document holds only the per-candidate evidence that contract needs as input.

Studies were conducted 2026-08-17 against vendor documentation, vendor source where public, and
the Orca reference checkout (`stablyai/orca`, cloned the same day).
A candidate's facts age at the speed of its CLI, so a study older than the descriptor it justifies
should be re-run rather than trusted.

## How to read a candidate entry

Capability is two independent axes, and the displayed tier is derived from them rather than
declared.
`state_sources` says which evidence channels can move lifecycle state (`hook`, `transcript`,
`pty`, `cli_state`).
`measurement_source` says where tokens, cost, context, and model come from (`transcript`,
`database`, or `none`).
A candidate's ceiling is therefore set by two questions that have nothing to do with each other:
can mux learn what the agent is doing, and can mux learn what the conversation cost.

| Tier | Condition | Product surface |
|---|---|---|
| `launchable` | no state source, no measurement | a PTY and nothing else |
| `identified` | measurements without a state source | conversation identity and figures, no lifecycle badge |
| `observed` | any state source | normalized status and delivery evidence |
| `hooked` | hook state without measurements | lifecycle from the CLI's own hooks |
| `managed` | hooks plus measurements | the complete surface |

Every claim in the underlying studies carries its provenance: read from source, read from vendor
documentation, read from a secondary source, or explicitly unknown.
A descriptor field must never be written from a secondary claim alone, because a wrong field
produces plausible, silent, wrong behaviour rather than an error.
Two candidates in this batch had a vendor document contradicted by shipped code, and both were
caught only because the study read the code.

## What a candidate has to clear

These are the existing gates, not new ones invented for this document.

| Gate | What it demands | Where |
|---|---|---|
| Declaration | a complete, self-consistent descriptor | `tests/test_harness_registry.py` |
| Contract | daemon and browser hold one registry | `test_generated_frontend_registry_seed_is_current` |
| Adapter | a launchable spawn spec, a resume spec carrying the declared resume tokens and the conversation id, graceful-exit keystrokes, and argv that survives Windows `list2cmdline` round-tripping | `tests/test_harness_adapter_matrix.py` |
| Dialect | a declared transcript dialect must parse a representative record into a message | `test_every_dialect_has_a_reader_that_actually_reads` |
| Behaviour | its own fixtures must reach `working` and `idle`, produce every normalized event it declares, produce a `proven` reading, reach `awaiting` with an `approval` sub-reason when it declares `approval_needed`, and produce an `inferred` reading plus a watchdog recovery when it declares a `pty` source | `test_status_matrix_is_covered_per_harness_not_just_corpus_wide` |
| Discovery | a declared answer for how mux finds conversations it wrote outside mux | `test_conversation_discovery_is_declared_for_every_harness` |
| Wiring | no harness name in generic code; every deviation is a descriptor field | `tests/test_harness_name_literals.py` |
| Live | a headless probe per tier the harness declares it can serve | `tests/test_live_agent_conformance.py` and siblings |

A capability a CLI genuinely lacks costs nothing at any of these gates, because each derives its
requirement from the descriptor.
What fails is an undeclared gap.

## Findings that cut across candidates

These change the plan rather than one entry, and they are the reason this study was worth running
before any descriptor was written.

### Claude's hook vocabulary has become the de facto standard

Seven of the nineteen candidates emit hooks whose event names, and often whose payload field
names, mirror Claude Code's: Copilot, Cursor, Kimi, Devin, Kiro, Qwen Code, and Crush by its own
documented compatibility claim.
Qwen Code is the clearest case, having rebuilt its hook contract to match Claude's rather than
keeping the Gemini CLI shape it forked from, down to copying Claude's `toolu_` tool-use-id format
verbatim.
Kimi's payload carries `hook_event_name`, `session_id`, `cwd`, `tool_name`, and `tool_input`.
Orca reached the same conclusion independently and organizes its test suites by vendor group
rather than by vendor.

The practical consequence is that one hook payload normalizer and one installer skeleton can serve
most of the batch, which is what moves several candidates from a high cost estimate to a medium
one.
It does not mean the events fire identically, and three candidates in this batch have open bug
reports about hooks not firing at all on some platform.
Shared vocabulary is a head start, not a guarantee.

### Per-session hook isolation is the real constraint, and mux is stricter than the market

Mux's hook identity model assumes per-launch injection.
Each pane gets its own settings file carrying its own ingress URL and secret, which is what stops
one pane's hooks being attributed to another and what makes a retired pane's credentials
unusable.
No candidate in this batch offers that.

The available mechanisms sort into five shapes, in descending order of safety.

| Shape | Scope achieved | Candidates |
|---|---|---|
| Per-launch flag or argument | one pane | none; only the five shipped harnesses |
| Launch-flag-selected config root | one pane, in effect | Crush (`--data-dir` selects the workspace-override tier), Kilo (`KILO_CONFIG_DIR`), MiMo (`MIMOCODE_CONFIG_DIR`) |
| Additive directory of merged files | one machine, non-clobbering | Copilot, Kiro |
| Project or workspace-scoped file | one project | Antigravity, Vibe, Qwen Code |
| Mutating a single shared global file | one machine, clobber risk | Cursor, Kimi, Droid, grok-cli, Hermes |

Only the second shape reaches per-pane isolation, and it does so by relocating a config root rather
than by naming a settings file.
Whether those relocations are additive over the user's own config or replace it is unconfirmed for
Kilo and MiMo, and getting it wrong silently drops the user's own plugins, MCP servers, and agents
from every mux-launched session.
That is the single highest-value live measurement in this document.

For the fifth shape, Orca's answer is to install once globally and demultiplex per pane, either by
an environment variable it injects into the child, or by matching its own managed command string
per hook event.
Hermes documents the constraint outright, stating that no session-only or single-launch override
mechanism exists.
Adopting that shape means accepting that a mux hook runs for every session of that CLI on the
machine, including ones mux did not start, and that per-pane attribution comes from a value the
hook happens to inherit rather than from an isolated credential.
That is a real weakening of the current model.
Phase 12 has to decide it deliberately, per shape, rather than discover it per harness.

### Conversation-id dictation is available exactly once

Claude's `--session-id` makes `record.native_session_id` authoritative from the first moment.
Among the candidates, only Qwen Code offers an equivalent.
Every other candidate mints its own id, and several have the request filed and unresolved
upstream: Antigravity's is open, and Kiro's was closed unimplemented after being filed by an Orca
contributor.
Cline is the trap in this group, because it accepts `--id` but treats it as resume-only and
silently self-mints on a fresh run.

So the mint-then-discover path is the normal case for expansion, not the exception.
The provisional-binding and corroborated-elimination machinery that exists for Codex is the
load-bearing code for Phase 12, and any estimate that treats expansion as descriptor authorship
alone is wrong.
Two candidates make discovery harder than Codex does: Devin's hook payload carries neither a cwd
nor a transcript path, so resolving a hook to its transcript means globbing a flat directory and
matching `working_directory` inside each file, and Antigravity's brain directory is flat with no
cwd encoding at all.

### Store-backed harnesses are now the majority pattern

Six candidates keep conversations in SQLite rather than in files: Goose, Crush, Kilo, MiMo, Kiro,
grok-cli, with Cline keeping a SQLite index beside per-session JSON.
The opencode precedent therefore generalizes further than expected, including the parts that were
written for opencode's specific hazards.
Reading read-only against a live WAL writer is confirmed available in Crush, which ships a
dedicated `ConnectReadOnly` path, and is claimed for Kiro by a third-party reader.
Deriving freshness from a watermark rather than a file stat matters more here than it did for
opencode, because several of these stores are one file per machine rather than one per project.

Goose is the standout on data quality and the weakest on liveness.
Its `sessions.db` carries exact `total_tokens`, `accumulated_cost`, provider, and model on the
session row plus a per-event `usage_ledger`, and it models subagents with a real
`parent_session_id` foreign key rather than the heuristics Claude and Codex force.
It has no lifecycle hook system at all, which caps it below `managed` regardless.

### The registry cannot express two shapes this batch needs

Two candidates cannot be declared today without a registry change, and both should be resolved
before the harness that needs them rather than during.

Crush resolves its conversation store to `.crush/crush.db` under the working directory, while every
descriptor resolves conversations under a per-user `data_home` and `conversation_store_path`
computes from it.
Crush does maintain a machine-wide `projects.json` registry mapping every project path to its data
directory, so discovery is answerable, but the descriptor field is not.

Aider has no conversation id in its data model at all, and `HarnessDescriptor.__post_init__`
rejects an empty `resume_argv`.
A harness with no resume concept currently cannot be registered.

### ACP is a second product surface, not a shortcut

The Agent Client Protocol registry lists a large number of these CLIs, which makes it look like a
generic adapter.
It is not one for this system.
An ACP agent is launched as a headless JSON-RPC subprocess and renders no terminal UI, so mux would
have to draw the conversation itself rather than attach to the CLI's own screen.
Droid's `droid.load_session`, which looked like a structured session API worth preferring over
transcript scraping, turned out to belong to exactly this surface.
ACP remains useful as a possible driver for live canaries, and is out of scope as an integration
route.

## Candidate summary

Ordered by recommended integration sequence, which weights cost and confidence rather than tier
alone.

| Candidate | Executable | Ceiling | Injection shape | Cost | Windows | Principal blocker |
|---|---|---|---|---|---|---|
| MiMo Code | `mimo` | `managed` | config-root env var | low-medium | yes | store schema drift from opencode unverified |
| Qwen Code | `qwen` | `managed` | project extension manifest | medium | yes | new reader; nothing else measured live |
| Copilot CLI | `copilot` | `managed` | additive hooks directory | medium | contested | `sessionId` in payload; ConPTY reports |
| Kilo CLI | `kilo` | `managed` | config-root env var | medium | yes | `KILO_CONFIG_DIR` additive or replacing |
| Crush | `crush` | `managed` | config root via `--data-dir` | medium | yes | only `PreToolUse` exists; registry cannot express its store path |
| Mistral Vibe | `vibe` | `managed` | project-scoped file | medium | second-tier | new dialect; vendor targets UNIX |
| Devin CLI | `devin` | `managed` | global file, maybe `--config` | medium | yes | payload lacks cwd and transcript path |
| Kimi Code | `kimi` | `managed` | global file, marker block | medium | yes | id dictation unconfirmed |
| Amp | `amp` | `hooked` | project plugin directory | medium | yes | conversations are server-side only |
| Antigravity | `agy` | `hooked` | workspace-scoped file | medium-high | yes | no local token or cost; `PreToolUse` approval hazard |
| Cline | `cline` | blocked short of `hooked` | global directory | medium-high | yes | `--hooks-dir` is dead code; `--id` is resume-only |
| Droid | `droid` | `managed` | global file only | medium-high | yes | no per-session scoping of any kind |
| Goose | `goose` | `observed` | none | medium | yes | no hook system exists |
| Cursor CLI | `cursor-agent` | `observed`, may fall to `identified` | global file plus env demux | medium-high | yes | hooks reported not firing; no local usage data |
| Hermes | `hermes` | `managed`, unproven | global file plus env demux | medium-high | unverified shape | store and hook id spaces may differ |
| grok-cli | `grok` | `managed` | single shared global file | high | ships a binary | no per-session lever; OpenTUI under ConPTY unknown |
| Kiro CLI | `kiro-cli` | unsettled | additive directory | high | unverified | tracker contradicts itself on whether CLI hooks fire |
| Aider | `aider` | `launchable` | none needed | low | yes | no conversation id exists |
| Rovo Dev | `acli rovodev` | `identified` | unknown | high | yes | requires an Atlassian org with paid credits |

## Candidates

### MiMo Code

Xiaomi's CLI is opencode republished, using the same `packages/opencode/src/` module tree, which
makes it the `pi` and `omp` relationship the registry was designed for: a second harness sharing an
existing dialect and store shape with its own adapter.
Session ids keep the `ses_` shape, resume is `mimo --session <id>`, and the plugin event bus is
opencode's, including `session.idle`, `session.status`, and the `permission.asked` and
`permission.replied` pair that gives a complete approval signal.

The store is `mimocode.db` under a `mimocode`-named data directory rather than `opencode.db`, so
the existing reader needs a path and filename parameterization at minimum.
Whether it needs more depends on whether the vendor's added features, which include persistent
memory and an orchestrator mode, touched the session or message tables.
That single schema diff decides whether this is the cheapest addition available or an ordinary one.

Licensing is MIT with a separate acceptable-use document, and the repository is a source-level hard
fork rather than a GitHub fork relationship.

Orca injects its plugin through `MIMOCODE_HOME`, which relocates the whole home including the
database, so Orca's own MiMo sessions write into a fresh empty overlay store rather than the user's
real one.
That is the wrong model to copy, and copying it would silently break both conversation discovery and
measurement.
`MIMOCODE_CONFIG_DIR` is config-only and is what mux should use, and proving it loads a plugin while
the session still lands in the user's normal data directory is a prerequisite, not a detail.

### Qwen Code

The most Claude-shaped candidate in the batch, and the only one that lets mux dictate the
conversation id.
It has `--session-id`, direct `--resume <id>` with no picker when an id is given, a native
`--fork-session`, cwd-scoped discovery under `~/.qwen/projects/<sanitized cwd>/chats/*.jsonl`,
`isSidechain` marking, a `subagents/<sessionId>/` layout, HTTP hook targets, and an additive
`--mcp-config`.
Every one of those is a capability that costs real adapter work elsewhere in this batch.

The seed hypothesis for this study was that Qwen Code, being a Gemini CLI fork, would share a
record dialect with Antigravity and let one reader serve both.
That is false.
Qwen rebuilt its hook contract and transcript envelope to mirror Claude's while keeping Google
GenAI's `role` and `parts` shape for the inner message payload, so it is a three-way hybrid needing
its own reader and sharing nothing with Antigravity.
Hooks are delivered through an installed extension manifest's `hooks` block rather than a per-run
flag, which is a new installer shape.

Nothing here has been verified against a live install, which is the main risk: the study is strong
on source and empty on runtime behaviour, including the terminal surface.

### GitHub Copilot CLI

The best hook surface in the batch by breadth: fourteen events including `sessionStart`,
`sessionEnd`, `userPromptSubmitted`, `preToolUse`, `postToolUse`, `postToolUseFailure`,
`preCompact`, `agentStop`, `subagentStart`, `subagentStop`, `permissionRequest`, and
`errorOccurred`, with `transcriptPath` on the four that need it and a native `http` hook type that
could post directly to the daemon's ingress without a shim command.
Hook configuration is a merged directory at `~/.copilot/hooks/*.json`, so mux adds its own file and
never touches the user's.
Records are per-session JSONL under `~/.copilot/session-state/<id>/`, with tokens and model in a
`session.shutdown` record, which satisfies the measurement leg.

Two things stop this being the obvious first pick.
The documentation says every hook payload carries `sessionId` and `cwd`, but Orca's production code
does not trust it and parses the id out of the transcript's `session.start` record instead, and
nothing in the study explains which is right.
Separately, four open issues report the new alternate-screen TUI misbehaving on Windows Terminal
and ConPTY, which is precisely the attach path mux depends on.
Both are cheap to settle against a real install and neither can be settled without one.

Cost is charged mostly to novelty rather than difficulty, since every piece has a working reference
in Orca to port from.

### Kilo CLI

A store-backed harness with an unusually complete event surface: `session.status` gives clean
`idle`, `busy`, and `retry`, and `permission.v2.asked` paired with `permission.v2.replied` gives
both halves of an approval, which Codex still cannot provide.
Native token, cost, and model columns sit on the session row, with per-step
`session.next.step.ended` events beneath them.
Conversation discovery is a store query keyed on a project id derived from the git root commit
hash, which is cleaner than any filesystem glob in this batch.

Resume is direct-by-id but the id must already exist, so the adapter needs the read-it-back-out
pattern rather than Claude's dictate-it-up-front pattern.
Hook injection is config-directory relocation through `KILO_CONFIG_DIR`, whose merge relationship
with the user's own global config is the unconfirmed fact that decides the whole integration
design.

### Crush

Reaches `managed` and is the only candidate whose config mechanism achieves genuine per-pane
isolation, because `--data-dir` is a launch flag and the workspace-override tier it selects wins
over both global and project config.
The store is SQLite with a purpose-built `ConnectReadOnly` path, running token and cost totals on
the session row exactly like opencode, and subagents modelled as child sessions carrying
`parent_session_id`.
Conversation discovery is a single small `projects.json` read that maps every project path to its
data directory, honouring any per-project `--data-dir` override.

Two constraints.
Its only hook event today is `PreToolUse`, with the vendor stating plans for more, so there is no
`Stop` equivalent and root-turn-completion has to come from the database rather than from a hook.
Its per-project store path cannot be expressed by the current `data_home` field, so it needs the
registry change named in the cross-cutting findings.

Licensing is worth stating precisely because it is unusual here: FSL-1.1-MIT, source-available
rather than OSI open source, with each version relicensing to MIT two years after release.
Reading it to write our own adapter is squarely permitted.
Copying code verbatim out of a release under two years old carries notice obligations, so any
literal reuse needs a decision rather than an assumption.

### Mistral Vibe

Both ingredients for `managed` are native: hooks (`post_agent`, `pre_tool`, `post_tool`) and
per-session measurement in a `meta.json` sidecar beside a `messages.jsonl`.
`post_agent` fires exactly once per user-turn cycle, when the assistant's last message has no
pending tool calls, which the study traced through the loop state machine rather than inferring
from documentation.
That makes it one of the cleanest root-completion signals in the batch.

Injection is project-scoped rather than session-scoped, through `<project>/.vibe/hooks.toml`
alongside the user's `~/.vibe/hooks.toml`, with project entries winning on name collision.
Two mux panes in one project share that file.
There is no approval raise-and-resolution pair, though `pre_tool` at least runs before the approval
prompt and can veto, which is better than Codex's silence.
Session records depend on logging being enabled, so first-run behaviour needs handling rather than
assuming.
The vendor states it officially targets UNIX environments while shipping real Windows support,
which makes Windows second-tier rather than absent.

### Devin CLI

A genuine local terminal agent in Rust, not a thin client to cloud sessions, with cloud as an
opt-in hand-off for long-running work.
Hooks are Claude-shaped, external commands receiving JSON on stdin, covering `SessionStart`,
`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PermissionRequest`, `Stop`, `PostCompaction`, and
`SessionEnd`.
Transcripts are plain JSON, and Orca already carries two real fixture shapes.

The shape is a hybrid mux has not built: Claude-like hooks and transcripts with Codex-like id
discovery.
The hook payload carries neither a cwd nor a transcript path, so resolving a hook's session to its
transcript means globbing a flat, non-cwd-bucketed transcripts directory and matching
`working_directory` inside each file.
Cost swings entirely on whether `--config <PATH>` composes per-session or is global-only: the
vendor's precedence documentation says hooks are collected alongside rather than overridden, which
suggests composition, but no source or Orca code confirms it.
The vendor does not support cmd.exe or conhost and requires Windows Terminal 1.25 or newer, which
needs a firsthand check against our own ConPTY host rather than an assumption in either direction.

### Kimi Code

Nineteen documented lifecycle events with a Claude-compatible payload shape, a fully specified
`wire.jsonl` record stream, and per-session directories under a working-directory bucket.
Model lives in a `usage.record` entry and tokens are summed from four usage fields; there is no cost
field anywhere.
Orca wires only seven of the nineteen events, so the unwired twelve are an Orca gap rather than a
CLI limitation, and their payload shapes are documented but not code-confirmed.

Injection is the global-file shape: a single `config.toml` with no per-launch flag and no additive
layer, so mux would own a marker-delimited block inside it, which is the pattern Orca already uses.
MCP needs the same read-modify-write treatment against a project-local `.kimi-code/mcp.json`, where
it can collide with a user's own MCP config for that project.
Whether `kimi --session <new-id>` creates a session under that exact id or falls back to minting is
unconfirmed, and it is the fact most likely to change the descriptor's shape.

The study also corrected the version: the current npm release is 0.36.1, not the 1.x implied by
secondary sources.

### Amp

The Windows question that would have disqualified it is settled and the answer is favourable.
The manual's line about Windows support being via WSL is stale.
The study installed `@ampcode/cli` in an isolated sandbox and ran the real native `amp.exe` on
Windows, resolving genuine Windows paths, and Orca's own mechanism for flagging agents that truly
require WSL does not list Amp.

The disqualifying fact is a different one.
Conversations are server-side only: `amp threads new` blocks on browser OAuth before it will mint a
thread id, `amp threads raw` is restricted to the vendor's internal users, and the vendor's security
documentation confirms threads live in their database rather than on disk.
Orca never built a transcript reader for Amp either, only an HTTP-push plugin.

So the plugin hook system is real and well-shaped, with `session.start`, `agent.start`, `agent.end`,
`tool.call`, and `tool.result`, project-scoped under `.amp/plugins/`, and resume is clean
direct-by-id.
But a mux Amp session would have an empty Transcript tab and no history search by construction, and
`managed` depends on whether `amp threads usage <id>` is cheap enough to call live.
That is a product tradeoff rather than a technical blocker, and it should be decided as one.

### Antigravity

Google's replacement for Gemini CLI, and on the measurement axis a downgrade from what it replaced.
The hook payload is the richest in the batch, carrying `conversationId`, `transcriptPath`,
`workspacePaths`, and `modelName` on every event, and `Stop` with `fullyIdle: true` is an
unambiguous turn-complete signal where `fullyIdle: false` means the run continues.
Hooks and MCP both support a workspace-scoped `.agents/` config that does not require touching the
user's global settings, which Orca does not use and which one live bug report contradicts on
Windows.

The transcript JSONL carries no token or cost fields.
Orca's code says so directly and Orca explicitly declined to read the internal store where usage
apparently lives, calling it unstable.
So `hooked` is the realistic ceiling.

The approval situation deserves its own decision.
`PreToolUse` is the only channel carrying approval information and is also the channel that returns
the permission decision, so an observational hook installed there risks becoming the authorization
outcome.
Orca does not install there at all and accepts having no live approval signal as the price.
Mux should declare no approval capability for Antigravity unless the behaviour of an omitted
decision field is measured and proven inert, because the failure mode is silently changing what the
user's own permission policy allows.

Conversation discovery is weak: the brain directory is flat with no cwd encoding, and project
scoping is only achievable through an ambiguity-refusing title and timestamp join.

### Cline

Richer than Orca's shallow treatment suggests, and currently blocked on two things.
It has a real external hook system with subprocess scripts and JSON on stdin, blocking pre-tool-use
control, a SQLite session index, per-session JSON transcripts carrying native token, cost, and model
fields, and three redundant subagent-distinguishing signals including a `parent_session_id` and a
`root__agentId` id shape.

`--id` is resume-only and a fresh run self-mints regardless of what is passed, so id discovery would
mean polling the session index for the newest row matching this pane, which is a race-prone pattern
mux has not needed elsewhere.
`--hooks-dir` and `CLINE_HOOKS_DIR` are documented as an additional hooks directory, and in the
checkout the CLI sets the environment variable and nothing reads it.
If that holds for the shipped npm package, there is no private injection point short of sandboxing
through `--data-dir` and `--config`, which also relocates provider and MCP settings.

### Droid

Clears `hooked` and `identified` cleanly, so `managed` on paper, and fails on injection.
There is no per-session hook scoping of any kind: no settings flag, no additive config env var, no
extension path.
The vendor documents configuration as the interactive settings menu or hand-editing
`~/.factory/settings.json`.
Orca mutates that single global file and disambiguates panes by matching its own managed command
string per hook event, which is not per-pane isolation.
A project-level `.factory/hooks.json` may exist as a middle ground, and whether it is additive over
the user's global hooks is undocumented and is the first thing to measure.

Two useful specifics came out of Orca's tests.
Droid is confirmed Windows-native by a real behavioural test rather than by a config path: it decodes
CSI-u directly and misreads the legacy escape-and-carriage-return fallback as a plain Enter, so Orca
sends CSI-u for both Shift and Ctrl Enter to Droid panes.
Orca's session discovery for Droid is not cwd-scoped and walks the whole sessions tree, and whether
the projects directory is actually subdivided on disk is unmeasured.
Orca also treats `PermissionRequest` as a real tenth event that the vendor's own documentation does
not list, tested against real fixtures.

### Goose

The inverse tradeoff to most of this batch: the best measurement data and no lifecycle hooks at all.
`sessions.db` carries exact `total_tokens`, `accumulated_cost`, provider name, and model config on
the session row, plus a per-event `usage_ledger` table, and subagent runs are correlated to their
root by a real `parent_session_id` foreign key rather than by heuristic.
That subagent model is better than anything currently in the registry.

Extensibility is MCP tool extensions only, fired by model choice rather than at lifecycle
boundaries.
The crate list has no hooks crate and Orca's hook registry covers fourteen harnesses with Goose
absent, so this is structural rather than a gap in the study.
`managed` is therefore unreachable and `observed` is the ceiling, contingent on whether its
`rustyline` REPL screen can be tailed in a PTY or its store polled for a timely liveness signal.
`identified` is the safe floor.

Two seed corrections: Goose ships real native Windows binaries, with Git Bash being install-script
delivery rather than a runtime requirement, and `block/goose` has been renamed to `aaif-goose/goose`.

### Cursor CLI

A hook state source exists on paper, with `stop` as turn-complete and tool events for working, but
multiple current bug reports say hooks silently do not fire at all on Windows or Linux for some
builds.
Orca's own production integration does not trust the hook payload for conversation identity, and its
provider-session dispatcher returns nothing for Cursor while returning real values for Claude,
Codex, Gemini, omp, and pi.
No native token, cost, or model data was found in either the on-disk JSONL or the headless JSON
output, so measurement is most likely absent entirely.

What is solid is storage and discovery: transcripts live at
`~/.cursor/projects/<slug>/agent-transcripts/<chat-id>.jsonl` where the slug is a deterministic
transform of the absolute cwd, which makes cwd-scoped discovery cheap and confident.
There is a startup trust-menu trap of the same family as Claude's that swallows bracketed paste,
with a known workaround.
Injection is the global-file shape, and Orca demultiplexes panes with an environment key it injects
into the child rather than anything the payload provides.

Whether this lands at `observed` or falls to `identified` depends entirely on whether hooks fire
reliably on Windows.

### Hermes

Plausibly `managed` and not provable from documents.
It has an in-process plugin hook carrying a session id, a complete approval raise-and-resolution
pair, a real turn-complete signal, and a `session_model_usage` table richer than most native usage
recording in this batch.

The blocking unknown is whether the store is populated by an ordinary interactive session rather
than only by the cron path that Orca's queries exercise, and whether the store's session id is the
same id space the hooks report.
If those diverge, the assumption that one session means one id means one set of measurements breaks,
and reconciliation logic is needed rather than plumbing.
Compression already mints new parent-linked rows for one logical conversation, which makes divergence
plausible rather than paranoid.

Injection is global-only, and the vendor states outright that no session-only or single-launch
override mechanism exists.
That makes Hermes the clearest statement of the constraint described in the cross-cutting findings:
it is a vendor design decision, not an Orca shortcut.
The store format has already changed twice, from JSONL to flat JSON to SQLite, so today's shape has
a real chance of moving again, and Orca's own on-disk session reader is probably already reading the
superseded layout.

Two operational facts matter more here than the tier does.
Four of the six core lifecycle hooks silently never fired until a fix landed around 2026-04-27, so
this harness needs a declared version floor rather than a capability claim alone.
And `hermes --tui` is a Node and Ink frontend over a Python gateway process speaking JSON-RPC on
stdio, while mux would spawn only the top-level process; the vendor documents an orphan-process bug
in that arrangement, with a child observed alive long after its terminal closed.
That lands directly on mux's job-object teardown, which is the mechanism responsible for not leaking
processes when a pane ends, and it should be measured before this harness is launched from a real
pane.
Installation is a self-contained script that provisions its own virtual environment and its own
managed Node, so the npm shim resolution mux uses elsewhere does not apply.

### grok-cli

Scope note: this is the community `superagent-ai/grok-cli`, per the explicit instruction to use the
non-gated one.
xAI's official Grok Build CLI is a subscription-gated beta and is out of scope.

Capabilities are real: hooks exist, and `~/.grok/grok.db` carries a `usage_events` table with input,
output, and total tokens, `cost_micros`, and model, cwd-scoped through a `workspaces` table.
Delivery is what blocks it.
Hooks and MCP both live in exactly one shared global file with no directory, no environment
variable, and no CLI flag, and project-level hook config is deliberately refused for stated security
reasons.
Every pane would mutate one file, which is a concurrency problem rather than an adapter problem.

Two warnings.
Orca's `src/main/grok/` module is not a usable template here: it disagrees with the community repo's
source on four independent points, and is almost certainly built against xAI's official CLI instead.
OpenTUI has no documented native Windows ConPTY integration, even though the project does ship a
native Windows binary built on a Windows CI runner.

### Kiro CLI

The least settled candidate, and the one where the tracker contradicts itself on the question that
matters most.
One open issue states flatly that hooks fire only in the IDE and not in `kiro-cli chat` or `kiro-cli
acp` sessions.
Two later and more specific reports assume or observe them firing, one of them from an Orca
contributor describing a working prompt-to-tool-to-stop sequence, with a separate open Windows bug
about `exit 2` failing to block.
The honest reading is that hooks probably do fire in the CLI, with an unresolved Windows-specific
blocking bug, and that only a real install can adjudicate.

Storage is similarly unresolved: a `conversations_v2` SQLite table in the TUI mode and a legacy JSONL
mode, with every path found being macOS or POSIX-generic.
No Windows path was found in any source, which is a hard blocker for writing the records half of a
descriptor.
The event names mirror Claude's, and Orca wires none of it, which the study correctly identifies as
unclaimed value rather than a reason for confidence.
`--resume-id` is documented and one reporter confirms it works regardless of cwd, while a cluster of
issues asks for exactly that capability, so which session store it attaches to is unconfirmed.

### Aider

The most-installed terminal coding CLI in the batch and the lowest ceiling in it.
There is no conversation id in the data model at all.
Resume is a boolean that reloads the single accumulating `.aider.chat.history.md` for the current
working directory, and that file is appended to on every run whether or not the flag is set.
Token counts and cost are tracked on the live object and never written anywhere machine-readable.
There is no hook, plugin, or extension system, and no MCP client.

So the honest answer is `launchable`, and the work should be scoped as declaring that and stopping,
not as writing a dialect or an adapter.
Two things are still worth doing.
The registry currently cannot express it, because an empty `resume_argv` is rejected.
And two mux panes in one project would interleave into the same history file, which
`--chat-history-file` can avoid by giving each pane a private path, worth doing purely to avoid
corrupting the user's own file.

### Rovo Dev CLI

Recommended for rejection, on access rather than on capability.
It requires an Atlassian organization with Rovo Dev enabled and a paid credit allocation, and the
vendor's own documentation states it is unavailable during a Rovo Dev Standard trial.
A swe-mux user without an Atlassian organization cannot run it at all.

Technically it would be `identified` at best: Orca's reader proves the JSON transcript parses but
extracts no token, cost, or model fields, while the adjacent reader for another vendor in the same
file does.
The vendor's hooks feature is undocumented, with one community thread describing it as preview and
advising against production reliance.
Orca's own code disagrees with itself on the executable, launching the bare word `rovo` in one file
while hardcoding `acli rovodev run --restore <uuid>` for resume in another.

## Examined and rejected

Recording these so a rejection is visible rather than a silence.

| Candidate | Reason |
|---|---|
| Gemini CLI | Retired for individual, Pro, and Ultra accounts on 2026-06-18. Superseded by Antigravity. |
| Rovo Dev CLI | Requires an Atlassian organization with paid credits; unusable for most users. |
| OpenHands CLI | Vendor states it is no longer actively maintained. |
| Roo Code | Repository archived May 2026; users migrated to Cline or Kilo. |
| OpenClaude | Unaffiliated hobby wrapper with no vendor and no adoption evidence. |
| OpenClaw | A general personal-assistant framework, not a coding CLI. |
| Ante | Alpha research preview, explicitly macOS and Linux only. |
| Autohand Code CLI | No adoption evidence; licensing aimed at large enterprises. |
| Prime Agent | Even Orca carries it as an unimplemented feature request. |
| MiniMax | Not a TUI harness; an encrypted web-cookie store in Orca, absent from its agent union. |
| Warp themes | A terminal colour-theme importer, not an agent. |
| Codex CLI module | An implementation detail of the existing `codex` integration, not a separate harness. |

Borderline, recorded without a study: Trae Agent, where the documented CLI and the open-source
repository may not be the same artifact; Continue `cn`, whose 2026 positioning leans toward
background and CI jobs rather than an interactive TUI; Codebuff, which has a genuine community at a
smaller tier; and Command Code, which has a working Claude-shaped hook system in Orca's source but no
independent adoption evidence.
Any of these earns a study if a user asks for it.

## Decisions Phase 12 must make

These are not research gaps.
They are choices that determine what the integrations are allowed to do.

1. **Whether mux will install hooks into a config file the user also owns.**
   Five candidates offer nothing else.
   The alternatives are accepting a marker-delimited block plus per-pane demultiplexing, or capping
   those harnesses below `hooked`.
   The decision should be made once, per injection shape, and recorded on the descriptor.
2. **Whether a harness with no local conversation records is worth integrating.**
   Amp is the case: real hooks, real status, and an empty Transcript tab and history search by
   construction.
3. **Whether to observe an approval channel that also decides the approval.**
   Antigravity's `PreToolUse` is the case, and the safe default is declaring no approval capability
   rather than risking a silent change to the user's permission outcomes.
4. **Two registry changes, made before the harness that needs them.**
   A conversation store resolved under the working directory rather than a per-user data home, and a
   harness with no resume concept, cannot be declared today.
