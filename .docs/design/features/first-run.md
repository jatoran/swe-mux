# First run: the experience tier and the setup sequence

## What it is

The first-run experience: one modal sequence (tier choice with theme, keymap, and a granular
Customize fold-out; then harness enablement with an install-wide fleet-access choice; then a
first-steps page that is the quest log itself), backed by machine-side flags so a choice made
on the desktop never reappears on the phone.
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
  The same rule shapes the granular view: `GET /api/experience-tiers` serves every tier and
  autonomy assignment plus the closed `overridable` key set, so the panel *draws* the policy
  rather than restating it, and the apply route accepts named deviations
  (`{tier, autonomy?, overrides?}`) so tier + refinements land in one atomic write.
  An unknown override key or autonomy level is refused with 422, never dropped, because a
  dropped override reads exactly like an applied one.
- **An unmade choice stays unmade.** `experience_tier` is `""` for every install predating
  the chooser and for a skipped first run; nothing stamps a tier nobody chose. The
  restart-scoped keys inside the terminal tier apply at the next daemon reload, and the
  first-run panel says so in place rather than restarting anything itself.

## Autonomy: the second axis

`AUTONOMY_LEVELS` in `experience_tiers.py` - `supervised`, `assisted`, `autonomous` - is an
absolute assignment over the prompt queue's auto-delivery keys (`auto_delivery_enabled`,
`auto_delivery_max_consecutive`, `auto_delivery_session_ttl_minutes`,
`auto_delivery_reply_window_minutes`) plus `agent_spawn_hourly_budget`.
It is deliberately orthogonal to the tier: how much swe-mux *watches* and how much an agent
may *act unattended* are different questions, and the second spends the operator's attention
rather than tokens.
`supervised` equals a fresh install (asserted the same way the deterministic tier is);
`assisted` turns the auto-delivery master on under the shipped bounds; `autonomous` widens
the bounds for long multi-agent work (10 consecutive sends, 2-hour idle grant, 1-hour reply
window, double the spawn budget) while every gate auto-delivery answers to - stability
window, quiet hours, the emergency pause, head-of-line order - still applies.
The chooser lives inside the tier page's Customize fold-out and is hidden for the terminal
tier, whose assignment already switches messaging off.
The high-throughput values are deliberately *not* shipped defaults: turning unattended
delivery on by default would cut against the floor that a non-human sender's write ends at a
human (`design/features/agent-messaging.md`), so they are one explicit choice away instead.

## Sequencing

`firstRunSurface()` (`frontend/src/tutorial.ts`) still arbitrates: the harness panel leads,
the tutorial waits. The tier step lives *inside* `HarnessSetup.tsx` as its first page,
shown only while `experience_tier` is `""`, so the arbitration gains no fourth surface.

The **theme** rides the tier page as one row using the same `ThemePicker` Settings renders,
previewed live as the catalogue is walked (`applyTheme`, nothing committed) and written on
Continue only when it differs from the shipped default - a skip hands the previewed screen
back. The **keyboard preset** rides the same page as one line, for the same reason and with
the same shape (`design/features/keybindings.md`): a preset is a defaults choice, it is
reversible from Settings, and it is exactly the question somebody arriving from tmux or
VS Code wants asked once. The line shows the highlighted preset's *warning* rather than
its description where it has one - choosing "tmux" takes Ctrl+B away from any tmux
running inside a pane, and after the choice is the worst time to find that out.
Skipping leaves `keymap_preset` at `""`, which behaves as the default preset; applying
the default explicitly is skipped too, because a first run should not write a
`keybindings.json` it has no reason to.

The **Customize fold-out** under the tier list holds the autonomy chooser, one checkbox per
overridable tier switch (seeded from the selected tier's served assignment; switching tiers
clears deviations, because a deviation is a statement about one tier's defaults), and the
two command-rail switches (`rail_enabled_desktop` / `rail_enabled_mobile`,
`design/features/ui.md`). It is drawn only when `GET /api/experience-tiers` answered,
because a hand-written seed would be the second policy copy.

The **agents page** adds one install-wide fleet-access line - "MCP tools + agent CLI
(recommended)", "MCP tools only", "Agent CLI + skill file", "No fleet access" - that fans
out to the per-harness `harness_mcp_enabled` / `harness_cli_enabled` /
`harness_skill_enabled` maps over every registered harness (`fleetAccessChanges` in
`HarnessSetup.tsx`), so a harness enabled later finds the choice already made.
The recommended answer writes nothing, because it is the empty map's own meaning.
The three per-harness checkboxes in Settings → Agents remain the granular editor
(`design/features/agent-skill-delivery.md`); this line is the one question most people
mean, answered once. It is hidden when the terminal tier was applied, whose assignment
already wrote every surface off.

Skip skips everything and writes only `harness_setup_complete`; "Configure in Settings…"
does the same and opens Settings → Agents.

## The quest log, capped at three

Three multi-step setups worth pointing at - voice (opens the guided setup, `voice.md`),
isolated worktrees (opens the Git tab), and connecting a phone (opens Settings → Remote) -
drawn as one card inside the empty workspace stage, the largest element on a new user's
screen and previously an inert one (usability audit finding 8).

The same card is the modal's **final page** ("SET UP::FIRST STEPS"): the same `QuestLog`
component over the same registry and the same machine-side dismissals, because mirroring is
done with components, never copies. A quest action on that page completes first-run and then
opens its surface, since the surface must not open under the modal. The completion flag is
written by whichever page exits - the final page, a quest action, or any skip - and *only*
then, because `firstRunSurface` arbitrates on `harness_setup_complete` and an earlier write
would unmount the panel out from under its own last page. The page is skipped entirely when
no quests are open.

The cap is the feature: a quest log that grows into a general todo list is an obligation
handed to a user on first launch, which is worse than not having one.
So the registry is a closed three-entry tuple in two places that must agree
(`frontend/src/questRegistry.ts` and `QUEST_IDS` in `src/swe_mux/config.py`), nothing
generates entries, and a fourth is a deliberate edit to both.

Completion is honest rather than tracked: the voice quest derives from the config keys the
guided setup writes (`tts_enabled`/`stt_enabled`), and the other two - which have no single
"done" signal a browser can read - complete only by explicit dismissal.
Dismissal is machine-side (`quests_dismissed`, validated against the closed set) and
permanent: nothing ever resurrects a dismissed quest, on this device or another, and there
is deliberately no Settings control to re-open one.

## Density follows the tier

The tier's one sanctioned frontend reader is `defaultHiddenDrawerTabs`
(`frontend/src/drawerVisibility.ts`): a pure-terminal install's drawer defaults to five
tabs (Notes, Files, Actions, Git, Alerts) instead of ten, putting away the agent-layer
machinery the tier switched off, and a deterministic install additionally puts Activity
away - its findings and timeline are fed by the model-backed layer that tier keeps off, so
the tab would mostly draw the three kinds of empty. The unchosen tier keeps the pre-tier
default (Processes only), so nothing shifts under an existing device.
Presentation, never capability - the tabs stay one context-menu toggle away - and the same
consultation rule as the shipped default: only a device with no stored visibility choice
derives from the tier, and a stored choice (including the empty set) is never overwritten.
A device with no stored choice re-derives on every config arrival, so re-applying a tier
from Settings changes the drawer live.

The left sidebar is deliberately not tier-shaped: its collapsed rail is five fixed
controls (projects, resources, Run, configurator, menu), which is not the overwhelm the
usability audit measured - the eleven-tab drawer is
(`development/USABILITY_AUDIT_2026-08-20.md`, scale table and finding 11).
The Run menu's advanced sections are the audit's finding 9 and stay a separate design
decision rather than a tier side effect.

## Key files

- `src/swe_mux/experience_tiers.py` - the tier table, the autonomy table, the overridable
  set, and their invariants.
- `src/swe_mux/routes/settings.py` - `POST /api/experience-tier`,
  `GET /api/experience-tiers`.
- `src/swe_mux/config.py` - `experience_tier` (choice-validated, hot).
- `frontend/src/HarnessSetup.tsx` - the three-step first-run panel, the tier copy, the
  Customize fold-out, and the fleet-access mapping.
- `frontend/src/Settings.tsx` - General → Experience tier (re-apply, reversible).
- `frontend/src/questRegistry.ts`, `frontend/src/QuestLog.tsx` - the quest log.
- `tests/test_experience_tiers.py`, `tests/test_quest_log.py`,
  `frontend/test/questRegistry.test.ts` - the contracts above.

## Relates to

- `development/USABILITY_AUDIT_2026-08-20.md` - the measured first-use findings this
  feature answers structurally.
- `design/features/setting-links.md` - the gated-surface invariant that makes "off"
  reversible in place.
- `design/features/agent-skill-delivery.md` - how agents learn the fleet surface the
  deterministic tier turns on, and the per-harness editor behind the fleet-access line.
- `design/features/auto-delivery.md` - the gates every autonomy level still answers to.
