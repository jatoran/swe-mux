# Agent Environment

## Purpose

Agent Environment is a session-selected, read-only inventory of the Claude Code or Codex CLI behind the focused terminal.
It answers which runtime options, tools, skills, MCP servers, plugins, hooks, agents, policies, and feature overrides can be discovered without changing the CLI.
It is not an execution surface and is not proof that a configured extension connected successfully.

The utility drawer owns a separate **Agent** tab titled **Agent Environment** immediately after Transcript.
Instructions and memory are the Agent tab's third segment, **Instructions**, titled **Instructions & Memory** in its body; it was a separate Project-scoped Context tab until the drawer consolidation. It carries no availability gate, so a shell session focused on the Agent tab still reaches it while Config and Tools — which read a live harness inventory — drop out.
Commands remains the action surface for inserting a skill or command into the focused terminal.

## What mux injects

When mux launches an instrumented agent it adds exactly two things per session, and both are removed when the session ends: its lifecycle hooks (which power status detection, history capture, and the prompt queue) and a read-only mux MCP server (which gives the agent fleet visibility and messaging).
The first-run panel states this, and this drawer is the per-session view of the injected hooks (each row's `owner` is `swe_mux`) and the MCP server.
Both are per-harness toggles under Settings -> Harnesses: turning off the MCP server removes only the agent's fleet surface, and "launch clean" (instrumentation off) launches the harness with no hooks at all, dropping it to unobserved with no status, history, or queue for its sessions.
The per-session and cleanup property holds for every harness family: Claude and Codex pass the hooks and MCP registration as launch arguments per spawn, and omp, opencode, and pi carry them in a session-private extension or config that is retired when the session ends.

## Inventory model

Every item keeps three independent axes:

- `scope`: `built_in | managed | user | project | local | session | unknown`;
- `origin`: the CLI, plugin, MCP source, or named configuration layer that supplied it;
- `state`: `documented`, `configured`, `enabled`, `disabled`, `available`, `restricted`, `shadowed`, or `restart_required` as applicable.

Two optional presentation fields sit alongside them.
`group` is an in-section heading the UI renders above the consecutive run of items that share it, so a section can be read by its own natural key rather than as one flat list.
`owner` names who installed an entry when that is knowable, and is `swe_mux` for the rows swe-mux provisions itself.
Only Hooks populates either today; both are generic so a section that gains a natural grouping does not need a new response shape.

Section `completeness` states how the inventory was obtained.
The UI must show that qualifier rather than presenting a passive scan as an exhaustive runtime registry.
In particular, configured MCP servers are not reported as connected, documented built-ins are not reported as loaded, and current skill files newer than the CLI generation are reported as requiring restart.

The response includes the retained launch executable, model and selected CLI options, current trusted working directory, current source metadata, and the CLI process-generation load time.
Configuration source paths are represented by opaque IDs and stable human labels.
`changed_after_start` compares a source mtime with the time the current CLI generation loaded, including a shell-to-agent promotion but not a conversation rollover inside the same process.

## Discovery and safety

`agent_environment.py` performs a bounded passive scan only when the tab or API is requested.
Results are cached for ten seconds per backend, working directory, provider home, launch arguments, model, and process generation.
The CLI version probe is a two-second `--version` invocation cached for one hour.
All blocking work runs outside the aiohttp event loop.

The scan reads only known JSON, TOML, Markdown, skill, command, and plugin-manifest locations.
Each configuration file is capped at 1 MiB, symlinks are refused, Markdown agent discovery is bounded, and each response section is capped at 256 items with an explicit truncation flag.
Malformed and unreadable sources become diagnostics instead of failing the complete inventory.

Opening or refreshing the tab never:

- starts or health-checks an MCP server;
- authenticates a connector;
- imports or executes plugin code;
- executes a hook command, or exposes its arguments, inline shell body, prompt, URL, environment, or credentials;
- writes provider configuration;
- claims that current files were loaded by the already-running CLI.

MCP endpoints omit credentials and query strings.
Stdio entries expose only the executable basename.
Policy values are limited to known non-secret keys and structured collections are summarized by count.

### Hook handler targets

A hook whose only identity is its event answers nothing: with both CLIs keying hooks by event, every row read `PreToolUse` and no row said what it ran or who put it there.
The event is therefore the `group` heading, and the row names the **handler target**: the program and the one script or module its command invokes.

The target is resolved structurally, never by quoting the command.
The first token is the program, unless it is a shell keyword, in which case the handler is reported as `inline shell` and its program is not named at all.
The first following token that is a `-m` module or is structurally a script path becomes the target; a flag, a `key=value` assignment, anything containing `://` or `@`, and anything naming a credential are all refused as candidates.
A handler with no identifiable target reports its program with the arguments explicitly withheld, and one with neither is an `inline shell command`.
The `Matcher` and `Timeout` a hook declares are shown because they are structural, not payload.

That line - the program and its script, never the argument list - is what makes the section useful without reopening what the safety boundary above closes: a hook command line is exactly where a user's own tokens and passwords sit, and none of them can reach a candidate target.

Hooks whose command runs `swe_mux.hook_client` carry `owner: swe_mux`, in a source checkout and inside the frozen desktop bundle alike (`desktop.py` re-dispatches `-m` itself).
That is the marker that makes swe-mux's own lifecycle reporting distinguishable from the user's hooks inside the same event, which is otherwise unanswerable from the payload.

## Surface

The header shows the selected session, backend and version, plus an explicit Rescan control.
Runtime identity is followed by compact counts and a warning when configuration changed after load.
Sections are collapsed disclosures with item count and completeness label.
Items that carry a `group` render under a sticky in-section heading for their run, so the event a hook belongs to stays readable while a long group scrolls; an `owner` renders as a chip on the row.
Policies open by default; a local substring filter opens matching sections and searches item identity, group, owner, origin, scope, state, description, and safe metadata, so `swe-mux` filters the Hooks section to the ones swe-mux installed.
Metadata values wrap rather than ellipsing, because the ones that overflow (a hook's script path, an MCP endpoint) are the ones worth reading and a touch device has no tooltip.

Configuration sources and diagnostics are separate disclosures below the capability sections.
The tab has no insert, send, enable, disable, connect, edit, or install action.
Shell sessions render a typed ordinary empty state and the endpoint returns `409`.

## Boundaries

Agent Context owns Project/global instruction bodies, learned memory, and deliberate root-instruction synchronization.
Agent skills owns the invocable skill inventory reused by Commands.

For OMP, the passive configuration scan includes native user and project settings, all four native
`.omp` MCP locations, optional root `mcp.json` files, and sibling `.mcp.json` files from explicit
extension-package arguments.
Its skill inventory mirrors the native, Claude, Codex, Agent Skills, GitHub, and managed-skill
providers that OMP 17.2.10 actually registers.
Cursor and Cline rule imports are not presented as skills.
The tool section uses OMP's documented 31-tool catalog and marks the tools normally mounted below
`xd://` in their descriptions, while still describing gated or conditional availability.
Agent Environment composes that skill discovery with passive runtime and extension metadata but does not return skill filesystem paths in its own normalized sections.
The provider CLI remains authoritative for actual runtime tool availability and connection health.
