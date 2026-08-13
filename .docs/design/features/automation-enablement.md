# Automation enablement

## What it is

Per-project opt-in for control-plane automations, gated by a dependency graph. Every
automation is enabled explicitly per Project; a consumer cannot run unless the full
transitive closure of the substrate it depends on is also enabled. Nothing runs on a
Project that did not opt in. Roadmap/vision context: `../../development/CONTROL_PLANE_ROADMAP.md`.

## Key concepts

- **Automation**: one registry entry with `id`, `kind` (`substrate` | `consumer`), `label`,
  `requires` (direct dependency ids), and `implemented`.
- **`implemented`**: false while an id is reserved with no code behind it. The toggle
  surface renders dependencies straight from this registry, so a placeholder edge presented
  as a complete dependency set would let a user switch on something that then does nothing.
  Enabling an unimplemented id is refused (`409 automation_not_implemented`).
- **Substrate**: the foundation consumers read from (`raw_store`, `tier0`, `scan_timeline`).
  It is inert in the sense that matters: none of it acts, notifies, or writes toward a session.
- **Substrate that spends**: `scan_timeline` costs bounded continuous calls only while one current run is explicitly enabled, which is why it is opt-in rather than ambient.
  Project context is user-owned data rather than an automation and never causes a model call (`project-card.md`, `scan-timeline.md`).
- **Consumer**: a feature assembled from substrate (`provenance_graph`,
  `declared_vs_verified`, `loop_detection`, `doc_debt`, `dead_end_memory`,
  `continuous_title`, `cross_session_interlocks`, `absence_report`, `attention_ranking`,
  `observation_inbox`, `screenshot_to_agent`).
  `observation_inbox` is a persisted compatibility id whose current label and surface are
  spawn-request review in Fleet Queue; the standalone human Observation Inbox is retired.
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
- Tier 0 capture, the deterministic consumers, and the scan timeline share one short TTL gate cache per Project root.
  Every Project-automation write clears that cache before the change event is emitted, so the drawer never waits for expiration after a toggle (`tier0-facts.md`, `deterministic-consumers.md`, `scan-timeline.md`).

## Toggle surface

Toggling a consumer shows the substrate it needs as a dependency line rather than a flat
checkbox, because a consumer whose substrate is off would otherwise read as
enabled-and-working:

- Enabling a consumer enables its whole transitive closure in the same action.
- Disabling substrate disables everything that reads from it, rather than leaving dependents
  enabled-but-inert.
- Unimplemented ids render disabled and labelled, never as ready to switch on.
- The file remains the source of truth; the editor is a two-way view over it and the write
  is revision-checked like every other project-config write.
- `scan_timeline` also exposes `scan_timeline_daily_budget_usd` in this editor.
  Project permission never enables a run; the current conversation must still be enabled from
  its Timeline tab.
- The Timeline tab exposes a Project-scoped Scan timeline shortcut.
  Enabling it adds `scan_timeline` plus `raw_store` and `tier0`; disabling it also disables consumers that depend on the timeline.
  This shortcut creates the blank Project context file but does not backfill or enable the current run.

## Configuration

- `<project>/.swe-mux/config.toml` → `automations` table (typed, non-secret, portable).

## API surface

```text
GET /api/projects/{project_id}/automations
PUT /api/projects/{project_id}/automations   {automations: {id: bool}, revision?}
PUT /api/sessions/{session_id}/scan-timeline/project   {enabled: bool}
```

`GET` returns the registry (id, kind, label, `requires`, `implemented`), the project's
`requested` table, and the resolution (`enabled`, `blocked` → missing dependencies). `PUT`
replaces the opt-in table through the ordinary project-config write: `409 revision_conflict`
on a stale revision, `409 automation_not_implemented` for a reserved id. The typed project
config endpoints (`GET|PUT /api/project/config`) still carry the same table.

## Key files

- Registry + DAG + resolver: `src/swe_mux/automation_registry.py`
- Per-project config field (parse/serialize/validate, `project_automations`): `src/swe_mux/project_files.py`
- Gate wiring + toggle routes: `src/swe_mux/server.py`
- Toggle surface: `frontend/src/ProjectsManager.tsx`

## Relates to

- `tier0-facts.md` — the first gated substrate consumer.
- `deterministic-consumers.md` — the model-free detectors gated by this DAG.
- `project-resources.md` — the `.swe-mux/config.toml` typed-options surface.
- `automation.md` — the OpenRouter observer/rule layer (separate mechanism).
- `scan-timeline.md` — the additional current-run grant, budget, and rollover contract.
