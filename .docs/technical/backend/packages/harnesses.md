# Backend: harnesses, adapters, and provider inventory

Index: `../packages.md`.
Design: `../../../design/features/backends.md`, `../../../design/features/provider-accounts.md`, `../../../design/features/agent-environment.md`, `../../../design/features/usage.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

## `harness.py`

Declared harness identity, capability axes, derived display level, delivery etiquette, tool catalogs, hook event sets, per-machine installation detection (`detect_installation`/`detect_installations`, via `shim_paths.which_real` plus the data-home signal), and the three-state launcher filter (`enabled_backends`).

**Not:** adapter process behavior, provider parsing, state arbitration, or enablement policy storage (`config.py` owns `harness_enabled`).

`tests/test_harness_adapter_matrix.py` fails when a harness is added to the registry with no adapter or spawn coverage.

## `adapters/`, `agent_launcher.py`, `hook_client.py`, `assets/omp_mux_hook.ts`

Provider command, resume, and transcript normalization; additive Claude and Codex lifecycle-hook launch wiring; adapter-owned worktree trust preflight and primary-root access argv; the packaged OMP in-process lifecycle extension; authenticated hook delivery and spooling; and relaying a daemon-composed permission decision to the CLI's stdout.

**Not:** public HTTP shapes, mandatory success of best-effort provider trust preparation, composing the decision shape itself (the shim imports nothing from the package and must stay a relay), or retrying a decision POST.

## `agent_skills.py`

Read-only discovery of the CLIs' own skills: per-vendor roots (user, repo, plugin, bundled), `SKILL.md` frontmatter, Claude command files, the Codex `agents/openai.yaml` policy, plugin enable-gating, shadowing, and a 10 s cache.

**Not:** writing or installing skills, speaking Codex's app-server protocol, or enumerating Claude's compiled-in built-ins, which is impossible from disk.

## `agent_environment.py`

A bounded passive inventory of one live CLI generation.

- Retained runtime options, known policy keys, feature overrides, source drift, and diagnostics.
- Documented built-ins, current skills, configured MCP, installed and configured plugins, and custom agents.
- Hooks grouped by lifecycle event, with their handler target and `swe_mux` ownership marked.
- A ten-second response cache and a one-hour version cache.

**Not:** starting or health-checking MCP, importing plugins, or executing hooks.
It never exposes hook command lines, arguments, inline shell bodies, environment, or credentials.
It never writes provider state, and never claims configured items are loaded or connected.

## `provider_accounts.py`

Saved auth snapshots, explicit switching, and safe quota reads.

**Not:** concurrent provider homes.

## `usage.py`

One bounded ccusage `--by-agent` collector, dynamic historical source normalization, cache migration, atomic last-known-good persistence, and collector freshness.

**Not:** harness launchability, saved provider-account identity, or quota telemetry.
