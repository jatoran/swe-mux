# Setting links and grant gates

## What it is

What a surface does when it cannot work because something is switched off.

Most of swe-mux's expensive or interruptive behaviour is off until someone turns it on, at one
of three levels: an install-wide setting, a Project's control-plane opt-in, or a device's own
alert profile.
A surface downstream of an off switch is inert, and inert looks exactly like empty.

This feature is the rule that it must not.
Such a surface states what is off, says what turning it on would do, discloses exactly what
turning it on would change, and **offers the switch in place**.
The deep link to the owning overlay remains, as the secondary control.

## Why a gate rather than a link

The link came first and was not enough.
Following one opened an overlay, left the reader to find the control, flip it, save a staged
draft, close the overlay, reopen the drawer, and reselect the tab they started on.
For the scan timeline it happened twice, because the Project permission only revealed itself
as a second gate after the install switch was already on: two full overlay round trips to see
one drawer pane.

The Land queue's verification gate never worked that way.
It states the block, shows the exact bytes that will run, and approves them in place with one
button that disappears once approved.
Everything here is that pattern generalised.

## Key concepts

- **Target**: one deep-linkable switch. It names the overlay that owns it, the Settings section
  when that overlay is Settings, and the `data-setting` id of the control.
  The catalogue is `frontend/src/settingTargets.ts`; ids are the daemon's own config keys for
  install settings and `automation:<registry id>` for a Project's opt-ins.
- **Grant**: a target that a gate may also *switch on* from where the reader is standing.
  `frontend/src/grants.ts` is the catalogue, keyed by the same ids, so one gate renders both
  the button and the owner's link and the two cannot drift.
  The switch's own key is *derived* from the target rather than restated (`grantKey`).
- **Scope**: how wide the change reaches - `install` (all projects), `project` (this Project),
  `device` (this device). A grant states its scope and where the change is written, every time.
- **Surface** (of a target): `settings` (install-wide configuration, including the global
  automation switches) or `project` (the Projects registry, the only per-Project editor).
  The Automation dashboard owns no switch: it shows the state of the global automation
  switches and links to them in Settings → Automation, so one switch has one owner.
- **Reveal**: `frontend/src/settingReveal.ts`. Opens any `<details>` above the marked control,
  waits for it to exist and to have a layout box, centres it in its scroller, flashes it, and
  focuses it. The disclosures are opened *before* the layout test, not on the way to the scroll:
  how a closed `<details>` hides its body is an engine detail (current Chromium skips it with
  `content-visibility`, which still reports client rects; a `display:none` implementation reports
  none), and under the second the wait would observe a control already in the DOM until its
  deadline expired.
- **Gate notice**: the standard shape a gated surface renders - `GrantGate`
  (`frontend/src/GrantGate.tsx`) for a surface that cannot function, or `GrantButton` for
  prose on a working surface that names a switch in passing.

## Invariants

- **A gated surface never renders as merely empty.** "No findings", "nothing ranked", "no
  clipboard entries" must be reachable only when the thing that produces them is actually on.
- **A grant only ever turns something on.** There is no revoking from a gate.
  Withdrawal stays with the owning editor, which is what keeps "one owner per switch" true
  while many surfaces can grant. `plan_grant` refuses a `false`, a `draft`, or an `off`
  (`grants.py`), and `test/grants.test.ts` refuses one in the browser catalogue.
- **A gate exists only while the surface is inert.** It is rendered in the off branch and
  disappears the moment the surface works.
  This is what keeps a Project-wide control from becoming a standing fixture in a
  session-scoped pane, which is the real content of the earlier rule that took the scan
  timeline's Project permission out of the Timeline tab (`test/scanTimeline.test.ts`).
  A **per-item** surface takes that further and carries no Project-wide gate at all. A Git
  Map row draws none of the land queue's three switches, including the install stop that
  does make its own Land button pointless: a row is repeated once per worktree, so a gate
  on it is the same block under each of eight expansions. They live once, in the landing
  strip at the head of that map, and a blocked row **sends the reader there** with one
  press (`land-queue.md`). That still satisfies "naming a switch obliges offering it" -
  the rule was written against a walk out to an overlay, not against a scroll to the top
  of the pane you are already on.
- **A gate is never hidden behind a disclosure.** A gate is what a surface renders
  *instead of* working, so folding one into a collapsed summary is the same defect as
  rendering the surface empty. The landing strip collapses everything except its install
  stop for exactly this reason, and its summary line goes on stating an unapproved
  verification command while closed.
- **Allowlisted at both ends.** `GRANTABLE_INSTALL_KEYS` and `GRANTABLE_PROJECT_VALUES`
  (`src/swe_mux/grants.py`) are closed sets, validated against `Config` and
  `PROJECT_CONFIG_FIELDS` at import; `frontend/test/grants.test.ts` holds the browser's
  catalogue against them. A renamed switch fails a test or startup, never a click.
- **Disclosed before the press.** Scope, where the change is written, the dependency closure
  it drags in, and whether it can cost money - all on the gate, not discovered afterwards.
  A Project opt-in says it travels with the checkout, because it is committed repository
  content that reaches every clone.
- **`spends` comes from the registry.** Whether an automation costs money is a field on the
  registry entry (`automation_registry.py`), carried in the payload, read identically by the
  Projects editor's chip and by every gate that offers the same switch.
  It is asked of the whole closure: `catch_me_up` is free and cannot be switched on without
  `scan_timeline`, which is not.
- **So does `needs_llm`**, on the same terms and for the same reason: one fact, one source,
  asked of the closure. It is a separate field because a model on the operator's own machine
  is a dependency with no bill (`automation-enablement.md`).
- **One request, whatever the mix of scopes.** `POST /api/grants` applies the Project write
  and the install write together. Sequencing them from the browser would mean two revisions,
  two failure modes, and a half-granted state whenever the second lost.
- **Validate everything, then apply.** A refused grant writes nothing at all.
  Within the endpoint the Project write goes first because it is the one that can fail (stale
  revision, read-only checkout, malformed file); the install write is validated `Config`.
- **One audit record per act.** `grant_applied` (`source="user"`) lists every scope-qualified
  key, the way an approved verification command leaves exactly one `land_verify_approved`.
  Without it a permission raised from a drawer pane would be indistinguishable, afterwards,
  from one that had always been on.
- **Naming a switch obliges offering it.** Prose that says "turn on X in Y" without a control
  is the defect this feature exists to remove; a `GrantButton` is the inline form.
- **A target points at a control that exists.** `frontend/test/settingTargets.test.ts` checks
  every target against the source that must carry its `data-setting`, every `automation:`
  target against the daemon's registry, and every *grantable* Project field against a control
  in the Projects registry - so a field the daemon enforces can never again ship with no way
  to set it.
- **And every `Config` field has a control or a stated reason it has none.**
  The test above walks from a *link* to its control, which catches a control renamed or moved
  and says nothing about a field that never had one.
  That is the gap thirty settings accumulated in by 2026-08-21: each shipped enforced by the
  daemon and reachable only by hand-editing `~/.mux/config.toml`, several of them load-bearing
  (what an agent may do to another session, how long the land queue holds a busy worktree, what
  a fresh attach replays), and two places in the app told the reader to go and find a control
  that did not exist.
  `frontend/test/settingsCoverage.test.ts` walks the other way, from every field the `Config`
  dataclass declares to a control in the Settings panel.
  A field with no control must appear on the `CONFIG_ONLY` list *with the reason*, which is the
  whole escape hatch: an entry there is a claim someone made on purpose, and "nobody got to it"
  is not one.
  Detection is deliberately narrow - the panel's own `change(key, value)` setter, a
  `BudgetControl`, a `data-setting` mark, or one of six named bespoke editors - because reading
  a field to render a label is not editing it, and a looser rule would pass the allowlist too.
  The same file holds the renderer fixture (`test/renderer/settingsConfigFixture.ts`) against
  `public_dict`, since a field missing from it renders as `undefined` in a control with no way
  to say so, and it had already drifted four fields behind `config.py`.
- **The reveal waits rather than firing once.** Settings fetches its bundle before rendering any
  tab, and a Project's opt-in list is a second fetch inside the panel, so the control is
  routinely absent at request time.
- **A marked control stays outside a collapsed disclosure.** Settings sections may fold a
  reference body behind a `<details class="settings-disclosure">` (`features/ui.md`), and the
  reveal does open one on the way in - `frontend/test/renderer/setting-reveal.spec.ts` pins that,
  so the rule is a convention rather than a load-bearing constraint. It holds anyway: a switch a
  gate has just promised should be on screen when the panel lands, not one state change further
  in. `frontend/test/renderer/voice-settings.spec.ts` checks it for the tab that folds most.
- **The flash is brief and identical everywhere.** One class (`.setting-flash`), shared with the
  settings search's own arrival cue, two pulses over 1.8s, reduced to a static outline under
  `prefers-reduced-motion`.
- **Centring, not nearest.** Both destination panels carry a sticky header inside the scroller;
  `block: 'nearest'` parks the control underneath it. Geometry is pinned for a desktop and a
  phone viewport in `frontend/test/renderer/setting-reveal.spec.ts`.
- **Focus follows the link, except into a keyboard.** The revealed control takes focus so the
  switch is operable immediately; on a coarse pointer a text field is left unfocused, because
  the on-screen keyboard would cover what the user was sent to read.
- **A Project grant with no Project refuses.** Naming the switch and then writing to some other
  Project would be worse than saying "select a Project first".
- **A failure keeps the gate up.** Nothing changed, so the surface must not claim otherwise.

## Coverage

Every surface that goes inert behind a switch, and what it offers.
"Grant" means the switch is turned on from the surface itself; "link" means the surface can
only route to the owning overlay.

| Surface | Switch | Level | Offers |
|---|---|---|---|
| Clipboard section (Actions tab) | `clipboard_history_enabled` | install | grant |
| Queue pane | `auto_delivery_enabled` | install | grant |
| Queue pane (grant lapsed for idleness) | `auto_delivery_session_ttl_minutes` | install | link (a value, not a switch) |
| Fleet Queue | `auto_delivery_enabled` | install | grant (inline) |
| Approval chip menu | `approval_auto_enabled` | install | grant (inline) |
| Schedule tab (list) | `scheduled_runs_enabled` | install | grant |
| Schedule tab (row, `install_disabled`) | `scheduled_runs_enabled` | install | grant (inline) |
| Schedule tab (row, `automation_disabled`) | `scheduled_runs` | Project | grant (inline) |
| Scan timeline (either switch off) | `scan_timeline_enabled` + `scan_timeline` | install + Project | grant, both in one act |
| Scan timeline (unarmed run) | `scan_timeline_auto_enable` | Project | grant (inline) |
| Automation dashboard (global switches) | `automation_enabled`, `scan_timeline_enabled` | install | link (it owns no switch) |
| Project settings (spending-limits prose) | `automation_daily_budget` | install | link (a value, not a switch) |
| Change map | `code_graph` | Project | grant |
| Findings pane (no detectors) | the four detectors | Project | grant, all four in one act |
| Findings pane (no observer notes) | `automation_enabled` | install | grant (inline) |
| Git → Provenance | `provenance_graph` | Project | grant |
| Git → Map landing strip (install stop) | `land_queue_enabled` | install | grant |
| Git → Map landing strip (agent authority) | `land_queue`, `land_grant` | Project | grant |
| Git → Map row, landing blocked | the two above | install + Project | opens the strip that holds them |
| Alerts tab (ranked inbox empty) | `attention_ranking` | Project | grant |
| Alerts tab (delivery muted) | device alert master | device | grant (local write) |
| Usage dashboard | `ccusage_enabled` | install | grant |
| Read-aloud chip | `tts_enabled` | install | link (a pane-bar chip, not a gate) |
| Talk toggle | `stt_enabled` | install | link |
| Claude width notice | `claude_max_columns` | install | link (a value, not a switch) |
| Files tab header (`ignores`) | `project_ignore_patterns` | install | link (a header control, not a gate; a value, not a switch) |
| Projects registry (model-backed row, provider unproven) | `llm_provider` | install | link (a value, not a switch) |
| Any gate over a `needs_llm` switch | `llm_provider` | install | link, disclosed beside `spends` |

## First use

Every automation is off for a new Project, which is correct as a rule and made every analysis
surface in the drawer inert on the first day.
Two things address that without weakening the rule:

- **A named starting set at Project creation.** `RECOMMENDED_PROJECT_AUTOMATIONS` is the
  model-free set - the four detectors and the code graph. It is offered as one checkbox on the
  creation form, defaulted on, and applied through the ordinary `POST /api/grants` so it leaves
  the same audit record as a gate press.
  `_validate_recommended` refuses at import to let a spending automation into it.
  It is deliberately **not** an inherited default template: it is written into that Project's own
  `.swe-mux/config.toml`, so "nothing runs on a Project that did not opt in" stays literally
  true and no existing Project changes behaviour because the constant did.
  `scan_timeline` is not in it - it is the one substrate that spends, and it is offered
  separately with its budget attached.
- **A tutorial step** (`gates`) that says the expensive things start off and that every notice
  turns its own thing on where you are standing.

## Deliberately not granted

- **A Project's approval ceiling.** `approval_ceiling` is a three-level policy field, and it is
  a *restriction* rather than an enablement. Lowering a safety ceiling from a chip menu is
  exactly the kind of act that should cost a deliberate walk to the Project's settings, so the
  approval chip states the restriction and grants only the install switch.
- **Values rather than switches.** A gate can honestly offer "turn this on"; it cannot offer
  "pick a number". Budgets, model ids, and width caps stay links.
- **The model provider.** Choosing OpenRouter or a custom endpoint, typing a base URL, a key,
  and a model id, and then verifying it is a configuration pass, not one press - so a gate
  over a model-backed switch *discloses* the unproven provider beside `spends` and links to
  Settings → Accounts. Granting the automation anyway is correct: the opt-in is a real
  permission and withholding it would mean the operator has to grant twice. What a gate must
  not do is report success and leave a switch that reads on and does nothing, which is why
  `GrantPlan.needs_llm` travels with `spends` and `POST /api/grants` returns the readiness
  verdict alongside what it applied.
- **Change map `unsupported` / `no_project`.** Neither is a switch: one needs a daemon build
  carrying the code graph, the other needs the session's directory registered as a Project.
- **Harness "launch clean" (`harness_instrument_enabled`).** A clean-launched session has no
  status detection, history capture, or prompt queue, and the surfaces that depend on those
  cannot currently tell that apart from an ordinary absence - the session payload does not
  report how it was launched. Settings warns at the point of the choice instead. Closing this
  needs the daemon to report per-session instrumentation.

## API surface

```text
GET  /api/grants     → {install: [key], values: {field: [allowed]}, automations: [...],
                        recommended_project_automations: [id]}
POST /api/grants     {install?: {key: true}, project_id?, automations?: [id],
                      values?: {field: value}, revision?}
                     → {applied: {install, automations, values}, spends, config, project?}
```

`GET` is the contract both ends check.
`POST` refuses with `not_grantable`, `grant_is_additive`, `unknown_automation`,
`automation_not_implemented`, `revision_conflict`, `project_config_malformed`, or
`project_config_read_only`, and applies nothing when it refuses.

## Implementation pointers

- `src/swe_mux/grants.py` — the allowlists, the plan, the additive rule.
- `src/swe_mux/automation_registry.py` — `spends`, `enabling_closure`, the recommended set.
- `frontend/src/settingTargets.ts` — the catalogue, the `mux:open-setting` channel.
- `frontend/src/grants.ts` — which targets are grantable, and the one applier.
- `frontend/src/GrantGate.tsx` — the gate block and the inline `GrantButton`.
- `frontend/src/settingReveal.ts` — wait, scroll, flash, focus.
- `frontend/src/SettingLink.tsx` — the deep link, now the secondary control on a gate.
- `frontend/src/projectAutomations.ts`, `frontend/src/installSwitches.ts` — the two cached
  reads that let a consumer surface tell "off" from "quiet", invalidated by
  `project_configuration_changed` / `configuration_changed` and by a local grant.
- `frontend/src/App.tsx` — `openSettingTarget`, the only place that decides which overlay
  opens and which others close.
- `frontend/src/Settings.tsx`, `ProjectsManager.tsx` — the two destinations; each takes
  `initialSetting` plus a `revealToken` that changes per request so the same link works
  twice. `ProjectsManager`'s `AgentAuthority` owns the four authority fields.
- `frontend/test/grants.test.ts`, `frontend/test/renderer/grant-gate.spec.ts`,
  `tests/test_grants.py` — the contract, the browser behaviour, and the refusals.
- `frontend/test/settingsCoverage.test.ts` — field → control, the `CONFIG_ONLY` reasons, and
  the renderer fixture's key set.
- `tests/test_settings_hot_apply.py` — the daemon half of the same honesty: a control whose
  help text says nothing about restarting must actually take effect on a running daemon.
