# Retired generated Project card design

Status: retired on 2026-08-13.
This document preserves the generated-card design for historical context only.
The active user-owned Markdown contract is `../../design/features/project-card.md`.

## What it is

A distilled, cached description of one Project — what it is, its main subsystems, and a
file → area map — in a few hundred tokens, built from the Project's own `.docs`. It exists
so a later cheap-model call judges "edited `processes.py`" against real architecture
instead of guessing from a filename. Built once per documentation fingerprint; several
consumers read the same card. Per-project opt-in and gated
(`automation-enablement.md`). Control-plane substrate step 4; vision:
`../../development/CONTROL_PLANE_ROADMAP.md` §5.4.

## Key concepts

- **Card**: `summary` (what the project is), `subsystems` (name + one-line purpose), and
  `areas` (the file → area map), plus the `fingerprint` it is valid for, the model that
  wrote it, and the overview file it came from.
- **Area**: the documentation page that claims a source file in its literal **"Key files"**
  section, named by its path under `.docs` (`design/features/sessions.md`). The routing
  table in `.docs/CLAUDE.md` is keyed by change *type*, which no machine can match to a
  path, so ownership is inverted from the per-doc Key-files sections — the same source and
  the same hub limit the doc-debt ledger uses (`deterministic-consumers.md`).
- **The map is deterministic, never model-written.** File paths go into the card exactly as
  the docs list them. Every compaction eval puts artifact/file tracking last among a
  summarizer's abilities (CP §2), so a paraphrased path list would be worse than none; the
  model is told the map exists and told not to restate it.
- **Fingerprint**: `sha256` over the card schema version, the prompt version, the model id,
  the overview file's path and bytes, the routing table's bytes, and the whole inverted
  area map. It is the card's identity and the whole invalidation rule.
- **Sources**, in order: the first of `.docs/00_OVERVIEW.md`, `.docs/OVERVIEW.md`,
  `.docs/design/architecture.md`, `README.md` that has content, plus `.docs/CLAUDE.md`, plus
  every doc's Key-files section. Each read is bounded to 32 KiB.

## Invalidation

**A card is valid only for the exact inputs it was built from.** Stated as a rule:

- The stored card carries its `fingerprint`. A card whose fingerprint does not match the
  Project's *current* sources is **never served** — not to a consumer, not as a fallback.
  It is left in the table as a cache entry and replaced on the next build.
- Anything inside the fingerprint therefore forces a rebuild: editing the overview, editing
  the routing table, a doc adopting or dropping a Key-files entry, adding or deleting a doc,
  changing the configured model, or bumping `PROMPT_VERSION` / `CARD_SCHEMA_VERSION`.
- **Nothing expires on a timer.** There is no TTL. A stale architecture summary presented as
  current is the silent-omission failure mode the design forbids (CP §2), and a clock cannot
  tell the difference between "old" and "wrong".
- A cheap stamp (file count, newest mtime, total size across `.docs/**/*.md`) is checked
  first so the common no-change case costs a stat walk rather than a re-read. The stamp can
  only cause *more* checking, never less: when it moves, the content fingerprint is
  recomputed and decides. Touching a file without changing it refreshes the stamp and spends
  nothing.

## Degradation

Every failure path yields **no card**, never a guessed one. A usually-wrong card poisons
every consumer that prepends it, and empty beats wrong (CP §7). No card when:

- no OpenRouter model is configured (neither `project_card_model` nor the automation cheap
  model), or the key is missing;
- the provider errors, times out, or returns an empty summary;
- the daily project-card budget is spent, or the `automation_enabled` kill switch is off;
- the Project documents nothing the card can be built from;
- the owning Project did not opt `project_card` in.

A provider failure is not retried for 5 minutes, so a project with no key does not issue a
failing request on every consumer call. Consumers that always prepend use `prompt_prefix`,
which is the rendered card or an empty string.

## Operations

- **Lazy.** Nothing is built until a consumer asks. An enabled project no consumer reads
  costs nothing; there is no loop, no event subscription, and no startup work.
- **Built once per fingerprint.** One model call (cheap tier, structured JSON schema
  `project_card_v1`), then a durable row plus an in-process memo. A restarted daemon reuses
  the stored row without spending.
- Concurrent consumers on one project serialize on a per-project lock, so a first request
  storm is still one call.
- Source reads, the docs walk, and the ownership inversion run off the event loop.
- **Metered on the shared automation budget ledger** under the `builtin:project-card` rule
  id, with an observer-call row like any other model call — the same pattern the read-aloud
  summarizer uses. Spend is visible next to the observers'.
- The rendered block caps the map (80 files / 24 areas by default) and **states what it
  dropped**; the full map stays queryable on the card object (`areas_for(path)`).

## Data model

- Table `project_cards` on the shared WAL `mux.db`: `project_id` (primary key),
  `project_root`, `fingerprint`, `card_json`, `schema_version`, `requested_model`,
  `resolved_model`, `input_tokens`, `output_tokens`, `cost_usd`, `created_at`. One row per
  Project, replaced in place — not pruned by retention, because it is bounded by project
  count and its validity is decided by the fingerprint rather than by age.

## API surface

- No HTTP route and no UI: the card is internal substrate for later consumers.
  `ProjectCardService` exposes `card_for_session(session_id)`,
  `card_for_project(project_id, project_root)`, and `prompt_prefix(session_id)`; all three
  resolve the per-project gate themselves, so no caller can read a card for a project that
  did not opt in.
- Build health (cached cards, builds, skips, last reason there was no card) is reported
  under `project_cards` in `GET /api/diagnostics/background`. "No card" is a legitimate
  outcome, so the reason has to be readable somewhere.

## Configuration

- Enabled via `automations = { project_card = true }` in `<project>/.swe-mux/config.toml`
  (see `automation-enablement.md`). It depends on no other automation: it reads the
  repository's docs, not Tier 0 or the raw store.
- Global settings: `project_card_model` (empty falls back to `openrouter_cheap_model`),
  `project_card_daily_budget_usd` (default 0.25), `project_card_max_input_tokens` (6000),
  `project_card_max_output_tokens` (600).

## Key files

- Sources, fingerprint, card, service: `src/swe_mux/project_card.py`
- Card row storage and the shared budget ledger: `src/swe_mux/automation_store.py`
- Key-files inversion and target normalization: `src/swe_mux/deterministic_consumers.py`
- Registry entry, gate resolver, construction, diagnostics: `src/swe_mux/server.py`

## Relates to

- `automation-enablement.md` — the opt-in DAG that gates the card.
- `deterministic-consumers.md` — shares the Key-files ownership inversion and its hub limit.
- `../../development/CONTROL_PLANE_ROADMAP.md` §5.5 — the scan timeline, the next consumer
  to read the card, and the reason it is a few hundred tokens rather than a document.
