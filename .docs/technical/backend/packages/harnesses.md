# Backend: harnesses, adapters, and provider inventory

Index: `../packages.md`.
Design: `../../../design/features/backends.md`, `../../../design/features/provider-accounts.md`, `../../../design/features/agent-environment.md`, `../../../design/features/usage.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

## `harness.py`

Declared harness identity, capability axes, derived display level, delivery etiquette, tool catalogs, hook event sets, per-machine installation detection (`detect_installation`/`detect_installations`, via `shim_paths.which_real` plus the data-home signal), and the three-state launcher filter (`enabled_backends`).

`probe_cli_version` is the registry's *presentation* of the shared probe in `cli_version.py`: the version token, so `version_is_untested` can compare it against a tested bound, with the CLI's own first line as the fallback when nothing in the banner parses as a version, and the exit status deliberately not consulted.

**Not:** adapter process behavior, provider parsing, state arbitration, running the `--version` subprocess or caching it (`cli_version.py`), or enablement policy storage (`config.py` owns `harness_enabled`).

`tests/test_harness_adapter_matrix.py` fails when a harness is added to the registry with no adapter or spawn coverage.

## `adapters/`, `agent_launcher.py`, `hook_client.py`, `assets/omp_mux_hook.ts`

Provider command, resume, and transcript normalization; additive Claude and Codex lifecycle-hook launch wiring; adapter-owned worktree trust preflight and primary-root access argv; the packaged OMP in-process lifecycle extension; authenticated hook delivery and spooling; and relaying a daemon-composed permission decision to the CLI's stdout.
That extension also publishes the running process's live MCP tool inventory to `MUX_RUNTIME_URL`, on its own route rather than through hook ingress, because it is not a lifecycle event.

`agent_launcher.py` additionally owns the console the CLI is about to run in, because it is the one process in that chain that is neither the shell nor the agent.
It holds `CTRL_C_EVENT`/`CTRL_BREAK_EVENT` so it can never be the link that stops waiting first, passes `CTRL_CLOSE_EVENT` and the logoff/shutdown events through (real terminations with a deadline), spawns through `Popen` rather than `subprocess.call` so the CLI's pid can be published while it runs, and adds the colour-forcing pair a shell's environment deliberately lacks.
It reports its own lifecycle to `MUX_SHIM_URL` at three moments (`started`, `child_started`, `exited`) from the normal return, an `atexit`, and the console handler, because the interesting exits are the ones that never run a `finally`.

**Not:** public HTTP shapes, mandatory success of best-effort provider trust preparation, composing the decision shape itself (the shim imports nothing from the package and must stay a relay), or retrying a decision POST.
Not deciding what its reports mean either - `console_contention.py` holds those rules - and never an argument *value* in a report, since an agent command line can carry a prompt.

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

**Not:** concurrent provider homes.

## `usage.py`

One bounded ccusage `--by-agent` collector (through `bounded_subprocess.run_bounded`, so the 10 MiB limit bounds memory while reading rather than describing an error after `communicate()` already buffered the answer), dynamic historical source normalization, cache migration, atomic last-known-good persistence, and collector freshness.

**Not:** harness launchability, saved provider-account identity, or quota telemetry.
