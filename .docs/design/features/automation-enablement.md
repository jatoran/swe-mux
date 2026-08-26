# Automation enablement

## What it is

Per-project opt-in for control-plane automations, gated by a dependency graph. Every
automation is enabled explicitly per Project; a consumer cannot run unless the full
transitive closure of the substrate it depends on is also enabled. Nothing runs on a
Project that did not opt in. Roadmap/vision context: `../../development/CONTROL_PLANE_ROADMAP.md`.

## Key concepts

- **Automation**: one registry entry with `id`, `kind` (`substrate` | `consumer`), `label`,
  `requires` (direct dependency ids), `implemented`, and `spends`.
- **`spends`**: whether switching it on can cost money. It rides the registry payload so the
  toggle surface's chip and every gate that offers the same switch read one fact from one
  source; previously it was documented only in this file and in comments, which meant a
  one-click grant could not tell the operator the thing they most need to know before
  pressing it. Asked of the whole closure, never of the named id alone: `catch_me_up` costs
  nothing and cannot be enabled without `scan_timeline`, which does (`enabling_closure`,
  `spends_money`).
- **`needs_llm`**: whether the automation cannot do its job without a language-model
  provider. Kept apart from `spends` even though the two coincide exactly today, because
  they answer different questions and a bring-your-own endpoint is precisely where they
  come apart: a model running on the operator's own machine is a dependency with no bill.
  `spends` is a *disclosure*; this is a *predicate*, and it is what `resolve` consults to
  decide a switch is inert. Import-time validation holds `spends` ⊆ `needs_llm`, since
  every way of spending money here is a model call.
- **`implemented`**: false while an id is reserved with no code behind it. The toggle
  surface renders dependencies straight from this registry, so a placeholder edge presented
  as a complete dependency set would let a user switch on something that then does nothing.
  Enabling an unimplemented id is refused (`409 automation_not_implemented`).
- **Substrate**: the foundation consumers read from (`raw_store`, `tier0`, `scan_timeline`).
  It is inert in the sense that matters: none of it acts, notifies, or writes toward a session.
- **Substrate that spends**: `scan_timeline` costs bounded continuous calls only while one current run is explicitly enabled, which is why it is opt-in rather than ambient.
  Project context is user-owned data rather than an automation and never causes a model call (`project-card.md`, `scan-timeline.md`).
- **Consumer**: a feature assembled from substrate (`provenance_graph`,
  `declared_vs_verified`, `loop_detection`, `doc_debt`, `dead_end_memory`, `prior_resolutions`,
  `continuous_title`, `cross_session_interlocks`, `absence_report`, `attention_ranking`,
  `model_narration`, `observation_inbox`, `screenshot_to_agent`, `session_control`,
  `phase_transitions`, `timeline_handoff`, `catch_me_up`, `live_blockers`,
  `semantic_history_search`, `scan_reads`, `scheduled_runs`, `land_queue`).
  `observation_inbox` is a persisted compatibility id whose current label and surface are
  spawn-request review in Fleet Queue; the standalone human Observation Inbox is retired.
- **Memory-read opt-ins (Phase 7.5)**: each cross-session memory MCP read is gated by the
  consumer whose output it reads - `provenance` by `provenance_graph`, `verified_status` by
  `declared_vs_verified`, `dead_ends` by `dead_end_memory`. `prior_resolutions` is the one that
  earns its own id (`requires ("tier0",)`), because it reads the experience corpus that no
  detector produces. Where the automation is off the tool returns `disabled`, never a fake
  empty (`mux-mcp.md`).
- **`scan_reads` (Phase 7.11)** gates the `scan_timeline` MCP read. It is its own consumer id
  rather than the `scan_timeline` substrate id, because a distilled intent summary is in some
  ways more revealing than the transcript excerpt behind it: a Project must be able to keep its
  timeline running and still withhold it from sibling agents, which gating on the substrate
  would make impossible. The sibling `scan_search` tool instead reuses
  `semantic_history_search`, the opt-in that already gates the identical query on the human
  surface. Being session-scoped, `scan_timeline` gates on the **target session's** Project
  rather than the caller's scoped Project set (`mux-mcp.md`, `scan-timeline.md`).
- **`session_control` (Phase 7.6)** gates a capability rather than a read, so it depends on no
  substrate (`requires ()`); the delivery-readiness predicate an interrupt gates on is
  intrinsic, not an opt-in. Since 2026-08-25 it is **on by default**: the registry's
  `default_on` marks it, `DEFAULT_ON_AUTOMATIONS` is the inherited template every resolution
  starts from, and a Project withdraws it with an explicit `session_control = false` -
  which the toggle surface and the grant path both *persist* rather than strip, because for
  a default-on id absence means on and a dropped false would silently re-enable. Default-on
  is reserved by an import-time check for exactly this shape: free, dependency-less,
  model-less capability gates whose every act is separately bounded and attributable -
  "nothing runs on a Project that did not opt in" still holds for everything that runs.
  The authority beside it also defaults open now: `session_control_grant` unset reads as
  `granted`, lowered by writing `draft` (a human approves every `interrupt`/`end_session` in
  the Fleet Queue) - and a malformed config falls to `draft`, never to the default, so
  corruption cannot widen an explicit narrowing (`mux-mcp.md`, `data-model.md`).
  The same automation gates the Project's `spawn_grant`, which does the identical
  granted-by-default split for agent-initiated `mux.requestSpawn` into that Project.
- **`land_queue` (Phase 14)** gates a capability rather than a read, so it depends on no
  substrate (`requires ()`) and is off by default. Its own id rather than a second meaning
  for `session_control`: that one acts on a *session*, this one moves a *repository's
  trunk*, and they deserve separate switches and separate budgets. Opting in is necessary
  and not sufficient - the Project's separate `land_grant` field stays at the inert `draft`,
  where an agent's `request_land` writes an approval row a human decides, until it is raised
  to `granted` (`land-queue.md`).
- **`scheduled_runs`** gates a capability rather than a read, like `session_control`, so it
  depends on no substrate (`requires ()`) and is off by default. Permission alone starts
  nothing: the schedules themselves are machine-local rows in the daemon's database, so a
  clone that inherits this opt-in has none of them, and the install-wide switch and
  concurrency ceiling still bound what does fire (`scheduled-runs.md`).
- **Consumer that spends**: `model_narration` and `continuous_title` are the consumers that cost
  model calls. Both, and the `scan_timeline` substrate under them, are also the whole of
  `needs_llm`, so they are exactly what an unverified provider holds back. `model_narration` depends on `attention_ranking`, so with ranking off there is
  nothing to narrate and no path to a call (`attention-ranking.md`). `continuous_title` (Phase 7.7
  adaptive titling) depends on `scan_timeline` and fires one cheap-model synthesis only on a genuine
  scope pivot, off by default (`automation.md`, `scan-timeline.md`).
- **Phase 7.7 scan-timeline consumers**: `phase_transitions` (a durable annotation on a work_phase
  pivot or a flat-novelty stall, feeding attention), `timeline_handoff`, `catch_me_up`,
  `live_blockers`, and `semantic_history_search` each `requires ("scan_timeline",)` and is
  model-free; they are cheap derivations over the scan spine (`scan-timeline.md`).
  Phase 7.11's `scan_reads` joins them on the same edge and is likewise model-free - it grants
  a read, and grants no path to a scan.
- **Enablement DAG**: `requires` edges. Import-time validation rejects cycles, dangling
  deps, and substrate depending on a consumer.
- **Resolution**: a requested opt-in set → `enabled` (deps satisfied) + `blocked`
  (id → missing transitive deps, for UI prompting) + `unverified` (deps satisfied, held
  back by something outside the DAG). Disabling a substrate node cascades
  its dependents to blocked (effectively off).
- **Verified-provider gate (Phase 15)**: `resolve(requested, llm_ready=...)` subtracts the
  `needs_llm` automations from `enabled` into `unverified` when the install has no proven
  model provider. Three things about that shape are deliberate:
  - It subtracts from `enabled`, never from `requested`. The free consumers layered over
    the timeline read records that already exist, so `catch_me_up` and `live_blockers` keep
    running when a key is rotated; failing the whole subtree would be a second outage
    caused by the first.
  - `unverified` is its own field rather than a `blocked` entry. `blocked` values are
    automation ids a grant can switch on, and no automation's enabling fixes an unverified
    endpoint - merging the two would render a gate offering to turn on nothing.
  - An automation already `blocked` by a missing dependency is not *also* reported
    unverified. It has one actionable answer, and offering two different fixes for one
    switch is how a toggle surface stops being read.
  The install-wide answer is `llm_endpoint.readiness()`; the daemon resolves it once per
  five seconds beside the per-Project gate cache and drops both whenever the endpoint is
  edited, a key is written, or a verification lands (`automation.md`).

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
- `scan_timeline` also carries `scan_timeline_auto_enable` and the Project context editor in
  this editor, because both are Project-wide and the per-run toggle in the drawer is not.
- An automation that spends is chipped as such. Marked rather than unmarked: most of the list
  is free, and a chip on every row would say nothing.
- Spending limits are **not** per-project. Scan timeline's budgets are global settings
  (Settings → Automation → Scan timeline); this editor is opt-in only. A `scan_timeline_daily_budget_usd`
  left in an existing Project file parses, is ignored, and is dropped on the next write.
  Project permission never enables a run; the current conversation must still be enabled from
  its Timeline tab.

## Agent authority

Four Project fields decide whether an agent still needs a human after the automation above it
is on. All four shipped enforced and unreachable - a line in a committed `.swe-mux/config.toml`
with no control in any overlay, which made the inert default impossible to discover and
impossible to change from the app; one of them told the agent to go and edit the file by hand
(`agent_messaging.py`). They live in the Projects registry beside the opt-ins they qualify,
because only an editor may take a permission away.

| Field | Default when unset (2026-08-25) | Lowered form | Gated by |
|---|---|---|---|
| `session_control_grant` | `granted` — acts directly | `draft`: a human approves each | `session_control` |
| `spawn_grant` | `granted` — creates sessions directly | `draft` | `session_control` |
| `land_grant` | `draft` — a human approves each (unchanged) | — (`granted` raises it) | `land_queue` |
| `interject_grant` | `granted` — may write mid-turn | `off`: waits for the queue | (delivery readiness) |

The three session-scoped authorities default open; landing a trunk still defaults to the
inert draft. In every case a malformed or unreadable config resolves to the *narrow* value
(`draft`/`off`), never to the default - corruption must not widen an explicit lowering.

`frontend/test/settingTargets.test.ts` holds every grantable Project field to having a control
here, so a fifth arriving the same way fails a test.

## Grants

Every opt-in above can also be switched on from the surface that needs it, through
`POST /api/grants`. That path is additive only - it can never turn an automation off - so the
editor here remains the single owner of withdrawal. The dependency closure is computed by the
daemon rather than sent by the caller, and the whole grant lands or none of it does.
The contract, the disclosures, and the refusals: `setting-links.md`.

## First-use starting sets

Three named sets are offered as checkboxes when a Project is created, served by
`GET /api/grants` (`project_starting_sets`) so the form and the daemon cannot drift.
Each is applied through the ordinary grant path as one POST for whatever was ticked, and none
is an inherited default template - each is written into that Project's own file, so "nothing
runs on a Project that did not opt in" stays literally true and no existing Project changes
behaviour because a constant did.

- `RECOMMENDED_PROJECT_AUTOMATIONS` (defaulted on): the model-free set - the four detectors
  plus `code_graph`, with `raw_store` and `tier0` under them.
  `_validate_recommended` refuses at import to let a spending automation into it.
- `LLM_PROJECT_AUTOMATIONS` (off): the model tier - `scan_timeline`, `continuous_title`,
  `model_narration` - whose closure drags in `attention_ranking` and the detectors under it;
  its values half (`grants.LLM_PROJECT_VALUES`) sets `scan_timeline_auto_enable` so the
  timeline arms per run. `_validate_llm_set` holds every member to `needs_llm` and the
  closure to `implemented`.
- `AUTONOMY_PROJECT_AUTOMATIONS` (off): `session_control`, `land_queue`, and - deliberately -
  `observation_inbox`, so whatever still drafts under the raised authority gets its review
  surface; its values half (`grants.AUTONOMY_PROJECT_VALUES`) raises `spawn_grant` and
  `land_grant` to `granted` and deliberately leaves `session_control_grant` and
  `interject_grant` at their inert defaults. `_validate_autonomy_set` holds it free to run.

The disclosures each checkbox owes, and why the exclusions are what they are:
`setting-links.md` § First use.

## Configuration

- `<project>/.swe-mux/config.toml` → `automations` table (typed, non-secret, portable).

## API surface

```text
GET  /api/projects/{project_id}/automations
PUT  /api/projects/{project_id}/automations   {automations: {id: bool}, base? | revision?}
GET  /api/automation/projects
GET  /api/grants
POST /api/grants   {install?, project_id?, automations?, values?, revision?}
```

`GET` returns the registry (id, kind, label, `requires`, `implemented`, `spends`,
`needs_llm`), the project's `requested` table, and the resolution (`enabled`, `blocked` →
missing dependencies, `unverified` → held back by the provider, plus the `llm` verdict and
its reason so the surface states it rather than paraphrasing). `PUT`
replaces the opt-in table through the ordinary project-config write, and takes the same two
guard shapes that write does (`features/projects.md`): `base` - what the caller believed
`automations` and `scan_timeline_auto_enable` held - writes only those two fields and
collides only when one of them actually moved, while a bare `revision` keeps the whole-file
check. Field-scoped is what the toggle list needs, because it shares one file with the
authority table and the repository options beside it and a whole-file guard made each of
those writes read as an external edit to the others. Refusals are `409 revision_conflict`
(naming the fields in `conflicts`) and `409 automation_not_implemented` for a reserved id.
Opting out of `scan_timeline` still clears `scan_timeline_auto_enable`, whichever guard was
used: a permission the Project gave up must not silently re-arm every run when it is granted
again. The typed project config endpoints (`GET|PUT /api/project/config`) still carry the
same table.
`GET /api/automation/projects` is the read-only fleet aggregation of the same per-Project
resolution — one row per registered Project, including Projects that opted into nothing —
drawn by the Automation dashboard's `projects` view so "what runs where" is answerable from
the surface named Automation. It has no write half: the revision-checked per-Project route
stays the only editor.

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
