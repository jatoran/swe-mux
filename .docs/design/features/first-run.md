# First run: the experience tier and the setup sequence

## What it is

The first-run experience: one modal sequence (tier choice, then harness enablement), backed
by two machine-side flags so a choice made on the desktop never reappears on the phone.
The experience tier is the product's answer to its own scale - 100+ commands, 200+ config
keys, 17 settings tabs - chosen once, as three genuine products rather than a good/reduced
ladder.

## The three tiers

Defined in `src/swe_mux/experience_tiers.py`; the boundary is one the architecture already
has ("almost everything is off until you ask"), so the tiers name the asking rather than
inventing a mode system.

- **Pure terminal.** Real terminals, zero opinions, nothing watching: no lifecycle hooks,
  no MCP registration, no shims, no fleet plumbing, no scheduled runs or land queue.
  This is a genuine product - the strongest claim against every tool that re-renders agents
  into its own UI - and the first-run copy is written so it never reads as the one you pick
  when you do not want the good features.
- **Deterministic.** Transcripts, status detection, managed harnesses and hooks, the agent
  fleet surface. Model-free throughout; byte-identical to a fresh install's defaults, which
  is asserted rather than trusted (`tests/test_experience_tiers.py`).
- **Automations.** Deterministic plus the model-backed masters: `automation_enabled`,
  `scan_timeline_enabled`, `attention_observers_enabled`. Budgets and per-Project opt-ins
  still apply; enabling the masters surfaces the existing gates, it does not spend anything.

## The rules that make a tier safe

- **A tier sets defaults; it never locks capability.** Applying one is a single absolute
  assignment of ordinary config keys, each individually editable afterwards, and every
  surface a tier turns off keeps its in-place switch (`design/features/setting-links.md`).
  No backend module may branch on `experience_tier` to decide what a user can do -
  enforced by `test_nothing_outside_the_tier_module_gates_on_the_tier`, with density
  defaults in the frontend as the one sanctioned reader class (presentation, not
  capability).
- **The assignment is absolute, not a delta.** Every tier writes the same key inventory,
  so switching tiers is deterministic whatever came before, and re-applying is idempotent.
  The stated cost: re-applying overwrites hand edits to exactly those keys, which is why
  the Settings control (General → Experience tier) applies on an explicit press.
- **The key sets are daemon policy.** `POST /api/experience-tier` computes and applies the
  assignment through the ordinary `update_config` path (validation, revision, hot/restart
  classification, `configuration_changed`); a browser-computed PATCH would be a second copy
  of the policy that drifts.
- **An unmade choice stays unmade.** `experience_tier` is `""` for every install predating
  the chooser and for a skipped first run; nothing stamps a tier nobody chose. The
  restart-scoped keys inside the terminal tier apply at the next daemon reload, and the
  first-run panel says so in place rather than restarting anything itself.

## Sequencing

`firstRunSurface()` (`frontend/src/tutorial.ts`) still arbitrates: the harness panel leads,
the tutorial waits. The tier step lives *inside* `HarnessSetup.tsx` as its first page,
shown only while `experience_tier` is `""`, so the arbitration gains no fourth surface.
Skip skips everything and writes only `harness_setup_complete`; "Configure in Settings…"
does the same and opens Settings → Agents.

## Key files

- `src/swe_mux/experience_tiers.py` - the tier table and its invariants.
- `src/swe_mux/routes/settings.py` - `POST /api/experience-tier`.
- `src/swe_mux/config.py` - `experience_tier` (choice-validated, hot).
- `frontend/src/HarnessSetup.tsx` - the two-step first-run panel and the tier copy.
- `frontend/src/Settings.tsx` - General → Experience tier (re-apply, reversible).
- `tests/test_experience_tiers.py` - the contracts above.

## Relates to

- `development/USABILITY_AUDIT_2026-08-20.md` - the measured first-use findings this
  feature answers structurally.
- `design/features/setting-links.md` - the gated-surface invariant that makes "off"
  reversible in place.
- `design/features/agent-skill-delivery.md` - how agents learn the fleet surface the
  deterministic tier turns on.
