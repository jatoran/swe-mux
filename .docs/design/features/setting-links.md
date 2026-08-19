# Setting links

## What it is

The path from a surface that cannot work to the switch that would make it work.

Most of swe-mux's expensive or interruptive behaviour is off until someone turns it on, at one
of three levels: an install-wide setting, a Project's control-plane opt-in, or a device's own
alert profile.
A surface downstream of an off switch is inert, and inert looks exactly like empty.
This feature is the rule that it must not: such a surface states what is off, says what turning
it on would do, and offers one control that opens the owning overlay, scrolls to the exact
switch, and flashes it.

## Key concepts

- **Target**: one deep-linkable switch. It names the overlay that owns it, the Settings section
  when that overlay is Settings, and the `data-setting` id of the control.
  The catalogue is `frontend/src/settingTargets.ts`; ids are the daemon's own config keys for
  install settings and `automation:<registry id>` for a Project's opt-ins.
- **Surface** (of a target): `settings` (install-wide configuration, including the global
  automation switches) or `project` (the Projects registry, the only per-Project editor).
  The Automation dashboard owns no switch: it shows the state of the global automation
  switches and links to them in Settings → Automation, so one switch has one owner.
- **Reveal**: `frontend/src/settingReveal.ts`. Waits for the marked control to exist and to have
  a layout box, opens any `<details>` above it, centres it in its scroller, flashes it, and
  focuses it.
- **Gate notice**: the standard shape a gated surface renders — a `.setting-gate` block holding
  the statement, the consequence, and one `SettingLink`.

## Invariants

- **A gated surface never renders as merely empty.** "No findings", "nothing ranked", "no
  clipboard entries" must be reachable only when the thing that produces them is actually on.
- **One link component.** Every "take me to the switch" control is `SettingLink`
  (`frontend/src/SettingLink.tsx`), which dispatches `mux:open-setting`; `App` routes it.
  Deep components (an approval chip in a pane bar, a findings pane inside a drawer body) get a
  link without a navigation prop threaded through every layer between.
- **Naming a switch obliges linking to it.** Prose that says "turn on X in Y" without a control
  is the defect this feature exists to remove.
- **A target points at a control that exists.** `frontend/test/settingTargets.test.ts` checks
  every target against the source that must carry its `data-setting`, and every
  `automation:` target against the daemon's registry, so a renamed control fails a test rather
  than stranding a link.
- **The reveal waits rather than firing once.** Settings fetches its bundle before rendering any
  tab, and a Project's opt-in list is a second fetch inside the panel, so the control is
  routinely absent at request time.
- **The flash is brief and identical everywhere.** One class (`.setting-flash`), shared with the
  settings search's own arrival cue, two pulses over 1.8s, reduced to a static outline under
  `prefers-reduced-motion`.
- **Centring, not nearest.** Both destination panels carry a sticky header inside the scroller;
  `block: 'nearest'` parks the control underneath it. Geometry is pinned for a desktop and a
  phone viewport in `frontend/test/renderer/setting-reveal.spec.ts`.
- **Focus follows the link, except into a keyboard.** The revealed control takes focus so the
  switch is operable immediately; on a coarse pointer a text field is left unfocused, because
  the on-screen keyboard would cover what the user was sent to read.
- **A Project target with no Project refuses.** Naming the switch and then opening the registry
  on some other Project's row would be worse than saying "select a Project first".

## Coverage

Every surface that goes inert behind a switch, and what it offers.

| Surface | Switch | Level | Link |
|---|---|---|---|
| Clipboard section (Actions tab) | `clipboard_history_enabled` | install | `clipboard.history` |
| Queue pane | `auto_delivery_enabled` | install | `queue.autoDelivery` |
| Fleet Queue | `auto_delivery_enabled` | install | `queue.autoDelivery` |
| Approval chip menu | `approval_auto_enabled` | install | `approvals.autoAnswer` |
| Schedule tab (list) | `scheduled_runs_enabled` | install | `schedules.install` |
| Schedule tab (row, `install_disabled`) | `scheduled_runs_enabled` | install | `schedules.install` |
| Schedule tab (row, `automation_disabled`) | `scheduled_runs` | Project | `project.scheduledRuns` |
| Scan timeline (install off) | `scan_timeline_enabled` | install | `automation.scanTimeline` |
| Automation dashboard (global switches) | `automation_enabled`, `scan_timeline_enabled` | install | `automation.engine`, `automation.scanTimeline` |
| Project settings (spending-limits prose) | `automation_daily_budget_usd` | install | `automation.budgets` |
| Scan timeline (Project not permitted) | `scan_timeline` | Project | `project.scanTimeline` |
| Change map | `code_graph` | Project | `project.codeGraph` |
| Findings pane | the four detectors | Project | `project.automations` |
| Alerts tab (ranked inbox empty) | `attention_ranking` | Project | `project.attentionRanking` |
| Alerts tab (delivery muted) | device alert master | device | `alerts.master` |
| Usage dashboard | `ccusage_enabled` | install | `usage.ccusage` |
| Read-aloud chip | `tts_enabled` | install | `voice.tts` |
| Talk toggle | `stt_enabled` | install | `voice.stt` |
| Claude width notice | `claude_max_columns` | install | `terminals.claudeWidth` |

## Deliberately not linked

- **A Project's approval ceiling.** `approval_ceiling` is a key in that repository's
  `.swe-mux/config.toml` with no control in any overlay, so the approval chip states the
  restriction and links only the install switch.
- **Change map `unsupported` / `no_project`.** Neither is a switch: one needs a daemon build
  carrying the code graph, the other needs the session's directory registered as a Project.
- **Harness "launch clean" (`harness_instrument_enabled`).** A clean-launched session has no
  status detection, history capture, or prompt queue, and the surfaces that depend on those
  cannot currently tell that apart from an ordinary absence — the session payload does not
  report how it was launched. Settings warns at the point of the choice instead. Closing this
  needs the daemon to report per-session instrumentation.

## Implementation pointers

- `frontend/src/settingTargets.ts` — the catalogue, the `mux:open-setting` channel.
- `frontend/src/settingReveal.ts` — wait, scroll, flash, focus.
- `frontend/src/SettingLink.tsx` — the one link control.
- `frontend/src/projectAutomations.ts` — the cached per-Project opt-in read that lets a
  consumer surface tell "off" from "quiet"; invalidated by `project_configuration_changed`.
- `frontend/src/App.tsx` — `openSettingTarget`, the only place that decides which overlay
  opens and which others close.
- `frontend/src/Settings.tsx`, `ProjectsManager.tsx` — the two destinations; each takes
  `initialSetting` plus a `revealToken` that changes per request so the same link works
  twice. The Automation dashboard is a link *source* only.
