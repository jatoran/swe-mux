# Backend: harnesses, adapters, and provider inventory

Index: `../packages.md`.
Design: `../../../design/features/backends.md`, `../../../design/features/provider-accounts.md`, `../../../design/features/agent-environment.md`, `../../../design/features/usage.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

## `harness.py`

Declared harness identity, capability axes, derived display level, delivery etiquette, tool catalogs, hook event sets, per-machine installation detection (`detect_installation`/`detect_installations`, via `shim_paths.which_real` plus the data-home signal), and the three-state launcher filter (`enabled_backends`).

`probe_cli_version` is the registry's *presentation* of the shared probe in `cli_version.py`: the version token, so `version_is_untested` can compare it against a tested bound, with the CLI's own first line as the fallback when nothing in the banner parses as a version, and the exit status deliberately not consulted.

**Not:** adapter process behavior, provider parsing, state arbitration, running the `--version` subprocess or caching it (`cli_version.py`), or enablement policy storage (`config.py` owns `harness_enabled`).

`tests/test_harness_adapter_matrix.py` fails when a harness is added to the registry with no adapter or spawn coverage.

## `model_catalog.py`

Asking a harness's own CLI which models it has, through the command that harness declares (`ModelSelection.catalog`), with a per-harness parser, a 15-minute cache, and `bounded_subprocess` around the run.
Also the substring narrowing and the "did you mean" suggestions a refused model gets.

The rule that governs the whole module: **it informs, it never refuses.**
A model absent from a listing still spawns, because every such listing lags the vendor that fills it - the failure `claude_models.py` had to grow a family fallback to escape.
The parser is declared per format rather than sniffed, because a parser that guesses at an unrecognized layout returns *fewer* models, and a short list is indistinguishable from a small account; an unparseable listing therefore returns nothing **paired with the command that produced it**, which is a diagnosis rather than a shrug.

**Not:** deciding whether a model may be launched (`harness.ModelSelection`), deciding whether one actually ran (`harness.model_agreement`), or holding a list of released models anywhere.

## `adapters/`, `agent_launcher.py`, `hook_client.py`, `assets/omp_mux_hook.ts`

Provider command, resume, and transcript normalization; additive Claude and Codex lifecycle-hook launch wiring; adapter-owned worktree trust preflight and primary-root access argv; the packaged OMP in-process lifecycle extension; authenticated hook delivery and spooling; and relaying a daemon-composed permission decision to the CLI's stdout.
That extension also publishes the running process's live MCP tool inventory to `MUX_RUNTIME_URL`, on its own route rather than through hook ingress, because it is not a lifecycle event.

`agent_launcher.py` additionally owns the console the CLI is about to run in, because it is the one process in that chain that is neither the shell nor the agent.
It holds `CTRL_C_EVENT`/`CTRL_BREAK_EVENT` so it can never be the link that stops waiting first, passes `CTRL_CLOSE_EVENT` and the logoff/shutdown events through (real terminations with a deadline), spawns through `Popen` rather than `subprocess.call` so the CLI's pid can be published while it runs, and adds the colour-forcing pair a shell's environment deliberately lacks.
It reports its own lifecycle to `MUX_SHIM_URL` at three moments (`started`, `child_started`, `exited`) from the normal return, an `atexit`, and the console handler, because the interesting exits are the ones that never run a `finally`.

**Not:** public HTTP shapes, mandatory success of best-effort provider trust preparation, composing the decision shape itself (the shim imports nothing from the package and must stay a relay), or retrying a decision POST.
Not deciding what its reports mean either - `console_contention.py` holds those rules - and never an argument *value* in a report, since an agent command line can carry a prompt.

## `shim_paths.py`

The one resolver for "what would this host actually launch under that name", and - since 2026-08-28 - the one place that can say why nothing would.

- `is_mux_shim` (content marker, per-host extension gate), `path_without_shim_dirs`, and the two memoizations that keep a PATH scan off the hot enablement path.
- `resolve_executable(command) -> ExecutableResolution`: the launchable path, or one of three *distinct* refusals - `not_found`, `mux_shim`, `windows_interop` - each with the path it refused and a `describe()` sentence naming what was searched, what was found, and what to do.
- `which_real` is that same call with the reason dropped, so there is exactly one resolver and no second implementation to disagree with it.
- `combine_resolutions` ranks two attempts at one logical command (`found` > `windows_interop` > `mux_shim` > `not_found`, ties to the caller's spelling) and carries every name either attempt covered onto `also_tried`, so a message describes the whole search rather than half of it.

The rule the three reasons exist to enforce: **a deliberate refusal and an absence must not be the same answer.**
Collapsing them into a bare `None` is what cost an operator an hour on 2026-08-28.
Told "No such file or directory: 'codex.exe'", they went looking for a missing install; the truth was that a working Windows codex had been found at `/mnt/c/.../npm/codex` and correctly refused.
The refusal is right - a Windows agent CLI driven from a Linux daemon writes its transcript into the Windows home, reports a `wsl.localhost` working directory, and joins no Linux process group.
One of those answers is actionable and the other sends you digging.

A refusal is logged at WARNING, once per distinct `(command, reason, rejected path)`; a find and a plain absence are DEBUG, because `detect_installations` re-resolves every registered harness on every registry read and an undeduplicated line would be the whole of `daemon.log`.
`clear_caches()` drops that de-duplication along with the memoizations, so a test asserting on a refusal is not silenced by a neighbour having provoked the same one.

**Not:** deciding what to do about a refusal. `provider_accounts` raises, `agent_launcher` exits with the sentence, and `harness.detect_installation` simply reports the harness absent; the resolver states the fact and nothing more.

## `agent_skills.py`

Read-only discovery of the CLIs' own skills: per-vendor roots (user, repo, plugin, bundled), `SKILL.md` frontmatter, Claude command files, the Codex `agents/openai.yaml` policy, plugin enable-gating, shadowing, and a 10 s cache.

**Not:** writing or installing skills, speaking Codex's app-server protocol, or enumerating Claude's compiled-in built-ins, which is impossible from disk.

## `agent_environment.py`

A bounded passive inventory of one live CLI generation.

- Retained runtime options, known policy keys, feature overrides, source drift, and diagnostics.
- Documented built-ins, current skills, configured MCP, installed and configured plugins, and custom agents.
- Hooks grouped by lifecycle event, with their handler target and `swe_mux` ownership marked.
- A ten-second response cache; the CLI version behind it is the shared probe (`cli_version.py`, one subprocess per resolved executable per five minutes, shared with the harness registry).
  This surface's own contract on top of it: the name check that refuses to run a binary the session did not say was its harness, the CLI's own line rather than a bare token because it is shown to a person, and a required zero exit because a fingerprint drawn from a failed run would change on every failure.

**Not:** starting or health-checking MCP, importing plugins, or executing hooks.
It never exposes hook command lines, arguments, inline shell bodies, environment, or credentials.
It never writes provider state, and never claims configured items are loaded or connected.
It also never stats a path that only a config file named: `_project_scoped_mcp_tables` drops `~/.claude.json` `projects` entries that carry no server before comparing any key, then matches what is left on the strings (`path_identity.same_path_lexically`), and only escalates to the filesystem when nothing matched.
Sweeping all 183 of this host's keys through `same_path` cost 367.7 s inside one request, because one of them was a UNC path into a stopped WSL distro - a stat against a recorded network path is a probe, and this tab probes nothing.

`resolve_mcp_servers` is the one seam that hands out an MCP entry's *raw* configuration, for the tool-catalog fetch alone.
It comes from the same walk that decides which row reads `shadowed`, so the configuration a fetch dials is always the one the CLI would use, and it never travels inside an API response.

## `mcp_tools.py`

Per-server MCP tool catalogs, collected only on explicit request, in four evidence tiers: swe-mux's own server read from `mcp.TOOLS`, OMP's live process snapshot, a `codex app-server` sidecar, and a direct dial of a Claude-configured server with the official `mcp` client.

- One cache keyed by a one-way config-content fingerprint, with per-key in-flight coalescing, `ttlMs`/`cacheScope` honoured, and a bounded entry count.
- The live-snapshot store for extension-published inventories, in memory and swept against live sessions.
- Sanitization and bounds on everything a probe returns.

**Not:** probing on tab open, dialling an HTTP server that carries credentials, persisting any reading, or describing a sidecar's health as the running CLI's.
The `mcp` client is imported inside the probe, so the daemon never pays for it at startup and its absence is a typed diagnostic rather than an import error; `packaging/swe_mux.spec` collects it explicitly for the frozen app.

## `provider_accounts.py`

Saved auth snapshots, explicit switching, and safe quota reads.
One-shot CLI invocations (login, status) go through `bounded_subprocess.run_bounded`; the Codex quota read is a JSON-RPC `app-server` over a held stdin and stays hand-rolled, because it is a conversation rather than a command.

Executable resolution is `shim_paths.resolve_executable`, and the suffix-stripping recovery for a stale `codex.exe`/`claude.exe` is **unconditional**.
It used to be gated on `os.name == "nt"`, which pointed exactly the wrong way: on Windows an `.exe` is at least plausible and PATHEXT usually answers anyway, while on POSIX it is certainly wrong - so the one host that could not possibly launch `codex.exe` was the one host that never tried the repair (2026-08-28).
A `mux_shim` or `windows_interop` refusal now raises rather than exec'ing the configured value anyway; running the binary the resolver has just refused is how the shim recursed into itself, and on WSL it is how a Windows CLI would be driven from a Linux daemon.

Every failure on that path is logged before it is raised - unstartable at ERROR with the resolution, timeout and nonzero exit at WARNING - because until 2026-08-28 none of them were, and a provider CLI that could not start existed only in the HTTP response body of whoever happened to ask.
What is logged is the resolution and the failure: the exit code and a `DIAGNOSTIC_TAIL_CHARS`-bounded **stderr** tail, never stdout, because stdout is where a token or a credential blob would be even when it is the only thing a failure printed.

**Not:** concurrent provider homes, or logging any provider payload.

## `usage.py`

One bounded ccusage `--by-agent` collector (through `bounded_subprocess.run_bounded`, so the 10 MiB limit bounds memory while reading rather than describing an error after `communicate()` already buffered the answer), dynamic historical source normalization, cache migration, atomic last-known-good persistence, and collector freshness.

**Not:** harness launchability, saved provider-account identity, or quota telemetry.
