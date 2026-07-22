# Automation enablement

## What it is

Per-project opt-in for control-plane automations, gated by a dependency graph. Every
automation is enabled explicitly per Project; a consumer cannot run unless the full
transitive closure of the substrate it depends on is also enabled. Nothing runs on a
Project that did not opt in. Roadmap/vision context: `../../development/CONTROL_PLANE_ROADMAP.md`.

## Key concepts

- **Automation**: one registry entry with `id`, `kind` (`substrate` | `consumer`), `label`,
  and `requires` (direct dependency ids).
- **Substrate**: captures facts but never acts or spends (`raw_store`, `tier0`,
  `project_card`, `scan_timeline`). Inert.
- **Consumer**: a feature assembled from substrate (`provenance_graph`,
  `declared_vs_verified`, `loop_detection`, `doc_debt`, `dead_end_memory`,
  `continuous_title`, `cross_session_interlocks`, `absence_report`, `attention_ranking`,
  `observation_inbox`, `screenshot_to_agent`).
- **Enablement DAG**: `requires` edges. Import-time validation rejects cycles, dangling
  deps, and substrate depending on a consumer.
- **Resolution**: a requested opt-in set → `enabled` (deps satisfied) + `blocked`
  (id → missing transitive deps, for UI prompting). Disabling a substrate node cascades
  its dependents to blocked (effectively off).

## Operations

- Opt-ins live in `<project>/.swe-mux/config.toml` under `automations = { id = bool }`.
  Unknown ids are rejected on write and dropped on resolve; non-boolean values rejected.
- Global config is only an inherited default template a Project overrides — there is no
  `rules.toml` that executes on every repo.
- Cross-project consumers (fan-out, absence report) are aggregators over the opted-in
  set, never global automations: a Project that never opted in contributes nothing.
- Enablement gating is distinct from config-value precedence. Once enabled, a setting
  value still resolves session/request → project → global-default.
- The Tier 0 capture gate is the first live consumer of this: capture runs for a session
  only when its owning Project has `tier0` effectively enabled (`../features/tier0-facts.md`).

## Configuration

- `<project>/.swe-mux/config.toml` → `automations` table (typed, non-secret, portable).

## API surface

- Read/written through the existing typed Project config endpoints
  (`GET|PUT /api/project/config`); no dedicated route.

## Key files

- Registry + DAG + resolver: `src/swe_mux/automation_registry.py`
- Per-project config field (parse/serialize/validate, `project_automations`): `src/swe_mux/project_files.py`
- Gate wiring (`tier0_enabled` resolver): `src/swe_mux/server.py`

## Relates to

- `tier0-facts.md` — the first gated substrate consumer.
- `project-resources.md` — the `.swe-mux/config.toml` typed-options surface.
- `automation.md` — the OpenRouter observer/rule layer (separate mechanism).
