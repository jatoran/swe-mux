# Documentation routing

- Changing process/session lifecycle or package boundaries: `design/architecture.md`,
  `design/features/sessions.md`, `design/features/backends.md`,
  `design/features/delivery-readiness.md`
- Changing what survives a crash rather than a restart - the durable session registry, terminal
  checkpoints, cold sessions, or whether an ended pane stays readable:
  `design/features/session-recovery.md`, `design/features/sessions.md`, `design/data-model.md`,
  `design/interfaces.md`, `technical/backend/packages.md`, `technical/backend/sqlite.md`,
  `technical/frontend/packages.md`.
  The rule the split exists to enforce: the PTY supervisor is the *primary* recovery path and stays
  near-frozen, so nothing here may be written from the supervisor process - a change there reaps
  every live session (`development/archive/SESSION_PRESERVING_RELOAD.md` §8).
- Changing the harness registry, descriptors, capability levels, adapter families, or adding a
  harness: `design/features/backends.md`; completed abstraction record:
  `development/archive/HARNESS_ABSTRACTION_AND_OMP.md`; per-surface parity classification and
  the open enforcement gaps: `development/archive/HARNESS_PARITY_AUDIT_2026-08-11.md`;
  per-candidate parity study for CLIs not yet in the registry, and the sequencing that consumes
  it: `development/HARNESS_EXPANSION_CANDIDATES.md`, `development/ROADMAP.md` Phase 12
- Changing the configurator agent - its generated inventory, its shipped guides, the gated
  MCP family, the launcher, or `default_harness`: `design/features/configurator.md`, plus
  `design/features/mux-mcp.md` for anything touching the tool contract.
  The rule the split exists to enforce: **nothing structural in that inventory may be
  hand-written.** Every section is derived from the registry that owns it, and the settings
  constraints are quoted from `_validate` by probing it rather than transcribed - a
  hand-maintained table beside a validator is a second validator, and the copy is what drifts.
  The second rule is packaging: the guides live under `src/swe_mux/assets/` because that is
  what the wheel and the PyInstaller bundle both carry, and `.docs/` is in neither. A guide
  moved into `.docs/` reads correctly from source and is silently absent for every user of
  the frozen app, which is the entire audience.
  The third rule governs **writes to a document this process cannot validate** (the
  per-device settings store, five of whose seven domains are the browser's schema):
  the write takes path-scoped operations, never a document, so everything it did not name
  is untouched by construction - the only safety available where validation is not - and it
  is guarded by a content digest the caller must have read plus a backup of the previous
  file. Whole-document replacement is the obvious way to give an agent this power and is the
  wrong one: nothing downstream can tell a mangled rail from an intended one, so the failure
  is silent and total.
  And the rule the *misattribution* taught (2026-08-24): every answer carries which Project
  the caller is standing in, and every per-Project fact is resolved to a Project **name**.
  A configurator launched into a foreign Project cannot derive its own Project id, so one
  override in a blob keyed by bare UUIDs reads as "this one" - which produced a confident,
  specific, wrong warning about another Project's rail button.
- Adding a tool to the mux MCP surface, or changing who may see one: `design/features/mux-mcp.md`,
  `src/swe_mux/mcp_contract.py`. A tool visible to a subset of sessions is listed *and*
  dispatched behind the same check (`design/features/configurator.md`, "The gate") - a tool
  advertised and then refused teaches an agent that the surface lies to it.
- Changing Project/Group registration, ownership, ordering, or sidebar visibility:
  `design/features/projects.md`, `design/data-model.md`, `design/interfaces.md`
- Changing Project notes, the global Scratchpad, files, ignores, or watches:
  `design/features/project-resources.md`, `design/data-model.md`, `design/interfaces.md`
- Changing Agent Context discovery, root instruction sync/restore, or its drawer surface:
  `design/features/agent-context.md`, `design/features/ui.md`, `design/interfaces.md`,
  `technical/backend/packages.md`, `technical/frontend/packages.md`
- Changing the session Agent Environment inventory, safety boundaries, or drawer surface:
  `design/features/agent-environment.md`, `design/features/ui.md`, `design/interfaces.md`,
  `technical/backend/packages.md`, `technical/frontend/packages.md`; runtime inventory research
  and planned collection strategy: `development/AGENT_ENVIRONMENT_RUNTIME_INVENTORY.md`.
  The rule the split exists to enforce: **opening the tab probes nothing**, and the one
  control that does reach a server (per-server Fetch tools, `src/swe_mux/mcp_tools.py`) is
  reached only by an explicit press. Everything it returns carries the evidence tier that
  produced it and those tiers are never collapsed into "connected" - a `parallel_probe` is a
  *separate* runtime with its own connection and authentication state, so its health is not
  the health of the CLI in the terminal, and for Claude it is strictly weaker than that CLI's
  own `/mcp` because dialling configuration reaches neither account connectors nor plugin
  gating. Two consequences follow. An empty catalog must say which kind of empty it is
  ("not probed", "not reported by this session", "connected and published nothing" are
  different facts that render identically otherwise), and an HTTP server carrying credentials
  is reported rather than dialled, because a probe would spend a credential the user handed to
  their CLI and not to this drawer.
- Changing trusted task imports, the Project Run menu, or task launch:
  `design/features/project-actions.md`, `design/features/projects.md`, `design/interfaces.md`,
  `technical/backend/packages.md`, `technical/frontend/packages.md`.
- Planning or implementing third-party plugin manifests, lifecycle, actions, panes, event hooks,
  callback authority, or marketplace discovery: `development/ROADMAP.md` Phase 25 and
  `development/PLUGIN_SYSTEM_FINDINGS.md`, then the affected substrate documents:
  `design/features/automation.md`, `design/features/meta-hooks.md`,
  `design/features/project-actions.md`, `design/features/mux-mcp.md`,
  `design/features/sessions.md`, `design/features/desktop-shell.md`, `design/interfaces.md`,
  `design/data-model.md`, `technical/backend/packages.md`, and
  `technical/frontend/packages.md`.
  The boundary is subprocess-only, and plugin event commands are separately approved EventBus
  consumers rather than legacy meta-hooks or a widening of canonical Universal hooks.
  Current shipped behavior and implementation paths are in `design/features/plugins.md`.
  **`.swe-mux/` and `.vscode/` are untracked as of 2026-08-28** and `.gitignore` now ignores
  each of them whole. Nothing under either - including the repository-authored
  `actions.toml`, `project-context.md`, and `prompts/`, which used to be deliberate
  exceptions - is carried in the repository any more; they are per-machine state. Read any
  doc that names those paths as describing the *format* a checkout may carry, never as a
  claim that this repository supplies one: a fresh clone declares no actions, imports no VS
  Code tasks, and starts with no project context.
  The rule that untracking forced, and the one to keep: **a test whose subject is a
  git-ignored path must distinguish "absent" from "broken".**
  `tests/test_project_actions_v2.py::test_this_repository_ships_actions_that_parse` reads the
  repository's own `.swe-mux/actions.toml` off disk, so it skips with a reason where there is
  no such file (a clone, CI) and still fails where one exists and does not parse - which is
  the only case that costs anyone anything. Collapsing those two into one green result is the
  regression to watch for, and it is what an `except` around the read would silently do.
  The suite was audited for others of this class by recording every git-ignored path any of
  the 5454 tests actually reads (2026-08-28). The only other one is
  `doctor_local._frontend_check` probing `src/swe_mux/static/index.html`, which is already
  written this way on purpose: the bundle is gitignored build output, so the check reports
  "source checkout, not built" rather than failing, and
  `test_doctor_local.py::test_every_category_the_daemon_report_emits_is_covered_or_declared_unchecked`
  asserts only that a category comes back either way.
- Changing the first-run sequence (the experience tier chooser, its key sets, the harness
  panel, the quest log, or `firstRunSurface` arbitration): `design/features/first-run.md`,
  `design/features/setting-links.md`, `design/interfaces.md`,
  `src/swe_mux/experience_tiers.py`.
  The rule the feature turns on: a tier sets defaults and never locks capability - no
  backend module may branch on `experience_tier`, which is enforced by a test, and every
  key a tier assigns is an ordinary config key with its own in-place switch.
- Changing panes, tabs, splits, drag/drop, or the mobile workspace projection:
  `design/features/workspace-layout.md`, `technical/frontend/workspace-state.md`
- Changing browser chrome, sidebar interaction, settings, focus, or overlays:
  `design/features/ui.md`, `technical/frontend/packages.md`
- Changing keyboard shortcuts - the chord vocabulary, sequences and the leader, the
  bindable command registry, the shipped keymap presets, `when` scopes, or what a host
  or platform is said to reserve: `design/features/keybindings.md`,
  `design/features/ui.md`, `design/features/first-run.md`,
  `design/features/desktop-shell.md`, `design/interfaces.md`,
  `src/swe_mux/keychords.py`, `src/swe_mux/keybindings.py`, `src/swe_mux/keymaps.py`.
  Four rules the design turns on, and none is a preference.
  **A chord names a physical key** (`KeyboardEvent.code`), because `.key` is the
  layout's answer - a binding recorded on Dvorak would move, and `Ctrl+Shift+5` reports
  `%`, so a `.key`-derived chord could never match a table written unshifted, which is
  exactly tmux's `prefix %`.
  **Only a binding's FIRST chord is judged against the host**, which is the whole
  practical argument for a leader: one interceptable chord buys 200 commands instead of
  one per command.
  **Nothing is refused for being reserved** except the fixed UI-scale chords. The
  previous list refused `ctrl+f` as browser-reserved while `Settings.tsx` was
  intercepting Ctrl+F in the same browser, so a chord is now accepted and *reported*
  per host - and a real measurement from the live browser
  (`frontend/src/hostKeyboardProbe.ts`) overrides the shipped table, where only a
  *tested* chord moves the answer.
  **Ctrl+Alt is AltGr on most non-US layouts**, which is why 24 of the 26 previous
  defaults fired while the user was typing; no shipped preset may use it, and a test
  says so.
  The registry and the frontend must describe each other in both directions
  (`tests/test_keybinding_registry.py`): a command the palette offers but nothing
  registers cannot be bound at all, and an id registered with nothing behind it accepts
  a chord and does nothing. Both had happened, silently, at the same time.
- Adding or moving an OpenRouter model setting, or changing how one is chosen or priced:
  `design/features/ui.md`, `technical/frontend/packages.md`, plus the owning feature's doc
  (`design/features/scan-timeline.md`, `design/features/voice.md`,
  `design/features/assistant.md`, `design/features/automation.md`).
  The rule, **reversed 2026-08-25**: every model setting is edited in Settings -> Accounts ->
  Models, rendered from `MODEL_ROUTES` by `ModelRoutingSummary`, and each feature tab keeps a
  read-only row naming its resolved model with a link back.
  The old rule put each control beside the feature it configured, on the grounds that a feature
  is set up in one pass.
  That optimised the wrong axis: changing *endpoint* is the operation that touches all seven at
  once, and under it the split meant four tabs of hunting to find out whether anything had
  broken, with no screen anywhere answering "what does this install call, and does the endpoint
  I just switched to even have it".
  Two controls writing one config key is still forbidden, which is why the feature tabs show
  these read-only rather than duplicating the picker.
  Whether a blank value is legal is still the distinction that matters: an **override** falls
  through to the cheap model, a **pin** is refused by the control (`required`), and the two must
  never render the same.
  Note that the guard tests here assert a *loop*, not seven static controls
  (`modelRouting.test.ts`, `settingsCoverage.test.ts` - which counts membership of
  `MODEL_ROUTES` as the control, because a generated key appears literally in no source).
- Adding a surface that goes inert behind a switch, changing how one reaches that switch
  (the deep link, the scroll-and-flash arrival, the `data-setting` marks), or adding a
  switch a gate may turn on: `design/features/setting-links.md`, `design/features/ui.md`,
  `technical/frontend/packages.md`; per-Project opt-ins themselves:
  `design/features/automation-enablement.md`.
  The rule the design turns on: a gate **grants** in place and can only ever turn something
  **on**, so many surfaces may switch a thing on while exactly one editor may switch it off -
  that asymmetry is what makes a write reachable from a drawer pane safe. Two things follow
  and are enforced rather than trusted: the grantable keys are closed sets checked against
  `Config` and `PROJECT_CONFIG_FIELDS` at import (`src/swe_mux/grants.py`), and every
  grantable Project field must have a control in the Projects registry
  (`frontend/test/settingTargets.test.ts`) - four authority fields once shipped enforced and
  reachable only by hand-editing a committed TOML file, which is the failure that test
  exists to prevent recurring.
- Changing what shows or hides the mobile soft keyboard: `design/features/ui.md`,
  `technical/frontend/packages.md`; open ask against the note editor:
  `development/CONTINUITY_TOUCH_KEYBOARD_ASK.md`
- Changing terminal input ownership across devices, the PTY WebSocket frames, or how a
  shared PTY is sized: `design/features/terminal-input.md`, `design/interfaces.md`,
  `design/features/sessions.md`, `design/features/ui.md`,
  `technical/backend/packages.md`, `technical/frontend/packages.md`; investigating delayed
  terminal input: `development/TERMINAL_INPUT_INCIDENT_RUNBOOK.md`
- Changing device presence (what counts as "in use", the heartbeat, the leading device):
  `design/features/device-presence.md`, `design/interfaces.md`,
  `design/features/notifications.md`, `design/features/terminal-input.md`
- Changing the utility drawer (tabs, their segments and sections, which of them are drawn,
  recursive desktop dock/launcher), Action rail placement, or where
  inserted text lands: `design/features/ui.md`, `design/features/workspace-layout.md`,
  `technical/frontend/workspace-state.md`, `technical/frontend/packages.md`;
  completed custom drawer layout implementation record:
  `development/archive/UTILITY_DRAWER_LAYOUT_IMPLEMENTATION.md`.
  The rule the segment registry exists to enforce: a surface folded into another tab keeps its
  own palette command and voice phrase, because a segment reached only by clicking has neither -
  so segments and sections are registered in `drawerSegments.ts` rather than held in a tab's
  local state, their selection persists per Project beside the tab's, and every retired command
  and tab id migrates forward (`keybindings.py`, `drawerLayout.ts`) rather than being dropped.
- Changing the Resources dialog (its four segments - processes, bandwidth, storage, fleet
  activity - or what any of them measures): `design/features/ui.md`,
  `design/features/processes-and-previews.md`, `design/features/remote-access.md`,
  `design/features/operational-telemetry.md`, `technical/frontend/packages.md`.
  The rule it turns on: the drawer's Processes *tab* is not made redundant by the dialog's
  Processes segment - a modal covers the terminal, and the tab exists to answer "what is this
  session running" beside it - which is the same watch-here/act-there split the prompt Queue
  has with the Fleet Queue.
- Changing the Usage dialog (its four segments - overview, agents, automation, quota - or how
  any spend figure is drawn): `design/features/usage.md`, `design/features/ui.md`,
  `design/features/automation.md`, `design/features/budgets.md`,
  `technical/frontend/packages.md`.
  The rule it turns on: **the three pots are never summed, and every figure carries its
  basis**. Agent spend is a subscription reconstructed from transcripts and is an estimate,
  automation spend is a metered key billed by the call, and quota is a share of a provider
  window and is not money - so no surface computes a total across them, and a figure drawn
  without its basis is the bug. Its corollary is the one that already shipped wrong once:
  agent spend has *two denominators* (ccusage over every transcript, and
  `provider_cost_dimensions` over only observed runs), so the subset is labelled by its
  denominator everywhere it appears and never presented as the agent total.
- Changing agent-skill discovery (which CLI directories are scanned, the metadata read from
  them, or how the Actions tab lists them): `design/features/ui.md`, `design/interfaces.md`,
  `technical/backend/packages.md`, `technical/frontend/packages.md`
- Changing the shipped swe-mux agent skill, `swemux --skill`, the skill installer
  (`install-skill`, its roots, or the removal recognition rule), or the automatic
  per-harness delivery (`harness_skill_enabled`, `skill_install_roots`, the `skill_delivery`
  capability, the Claude plugin dir, or `deliver_project_skill`):
  `design/features/agent-skill-delivery.md`, `design/features/backends.md`,
  `design/interfaces.md` (CLI section),
  `technical/backend/packages/daemon-runtime.md` (`cli.py`), `packaging/swe_mux_cli.spec`.
  Three rules the feature turns on: the skill enumerates no commands (the binary's `--help`
  is the authority, so the skill cannot go stale); a global skill root is written only by
  the explicit command after printing its plan - project scope is the widest anything
  automatic may ever reach, and automatic project writes are off by default because they
  land in the user's checkout; and the two delivery routes are never presented as symmetric,
  because Claude's is a session-scoped plugin that touches no checkout while every other
  CLI's is a tree write that exists only because no flag/env/config route does.
- Changing clipboard capture, the clipboard-history ring/panel, or where inserted text lands:
  `design/features/ui.md`, `design/interfaces.md`, `design/data-model.md`,
  `technical/frontend/packages.md`, `technical/backend/packages.md`,
  `technical/backend/sqlite.md`, `design/features/remote-access.md`
- Changing Windows desktop packaging, WebView, tray, login startup, or daemon shutdown:
  `design/features/desktop-shell.md`, `design/architecture.md`, `design/interfaces.md`,
  `design/features/remote-access.md`, `technical/backend/packages.md`
- Changing how swe-mux is *started* without a terminal - the first-run shortcut offer
  (`first_run.py`), the detached `swemux start` (`daemon_start.py`), the shortcut slots
  (`shortcuts.py`, `routes/desktop_integration.py`), or which of the two run-at-login
  mechanisms is written or read: `design/features/desktop-shell.md`, `design/interfaces.md`
  (CLI and the desktop-integration endpoints), `development/OPERATOR_LIFECYCLE.md`,
  `README.md`, `site/tools/docs_content.py` (then rebuild `site/`).
  Two rules this area keeps, both learned the same day. **A capability the product is
  started by is not an optional extra** - the tray's dependencies are base requirements
  because `[project.gui-scripts]` ships the `swe-mux` launcher into every install regardless
  (`development/ROADMAP.md` § Phase 24, "Superseded the same day"). And **run-at-login has
  two mechanisms** - an `HKCU\...\Run` value and a `shell:startup` link - so anything that
  reports the setting reads both, and anything that turns it off clears both.
- Adding, renaming or removing a launcher (`[project.scripts]`, `[project.gui-scripts]`):
  `pyproject.toml`, `src/swe_mux/install_location.py` (`SHIPPED_COMMANDS`, `CLIENT_COMMANDS`,
  `DAEMON_COMMANDS`), `cli.LAUNCHER_NAMES`, `__main__.DAEMON_LAUNCHER_NAMES`,
  `packaging/swe_mux_cli.spec` + `build_desktop.CLI_EXES`, `packaging/installer/swe-mux.iss`,
  and then every document that names one.
  **The commands are `swemux`, `swemuxd` and `swe-mux`, one per program, and there are no
  aliases on purpose.** `mux`/`muxd` existed from 2026-08-29 to 2026-08-30; `pyproject.toml`
  carries the argument and `development/ROADMAP.md` § "Taken into 0.1.3" carries the
  correction, which is worth reading before adding a short name back - "purely additive" is
  a claim about the code and not about the name.
  `tests/test_launcher_names.py` fails on any code span that names a launcher this project
  does not ship, over every markdown and source file including `site/tools/docs_content.py`,
  whose string literals are published code blocks and are scanned specially.
- Changing which static tree the daemon serves, the frontend overlay, its verification, its
  compatibility pin, or its revert: `design/features/desktop-shell.md`,
  `design/interfaces.md`, `technical/backend/packages/daemon-runtime.md`,
  `technical/backend/packages/routes.md`, `technical/frontend/packages/composition.md`.
  Three rules the split exists to enforce, and none of them is optional - an overlay without
  all three is the hacky version of a mainstream pattern rather than the sound one.
  **`FRONTEND_DIR` is resolved in exactly one place** (`server.create_app`) and read by four
  sites inside it plus three route modules; a second reader that learned about overlays would
  be a second decision, and the ones downstream would serve a different tree than the status
  endpoint reports.
  **The compatibility pin is the producer's claim, checked for exact equality**, so
  `packaging/build_frontend_overlay.py` stamps it from the checkout and the daemon refuses a
  payload that arrived without a manifest rather than inventing one - and the rule that
  follows, *an app update always supersedes an overlay*, is what makes a stale overlay
  self-clearing instead of a cleanup step somebody has to remember.
  **A revert must be reachable without the frontend**, because the failure mode being guarded
  against is a frontend that will not load; `swemux ui-overlay revert` is that path and the
  endpoint is the convenience.
  And the thing that will bite: **precompressed `.gz` sidecars are derived, not payload.**
  The daemon writes them into whatever tree it serves, so a verification rule that treated
  every unlisted file as a fault would pass an overlay's first start and fail its second.
- **Adding, removing, or upgrading any dependency** - Python or frontend:
  `packaging/license_audit.py` (the allowlist and the closure walk),
  `THIRD-PARTY-NOTICES.md` and `packaging/third_party_licenses.json` (both generated,
  never hand-edited), `design/features/desktop-shell.md`, `CONTRIBUTING.md`.
  The rule the gate exists to enforce: **a dependency's declared license does not describe
  what its wheel ships.** PyAV declares BSD-3-Clause and links GPL x264/x265; sherpa-onnx
  declares Apache-2.0 and statically links espeak-ng. So the check has two halves that must
  both stay alive - `license_audit.py --check` reads the resolved closure's metadata and runs
  in the verification gate, and `build_desktop.verify_bundle_licenses` reads the built tree
  for the payloads by artifact name. Neither substitutes for the other.
  GPL and AGPL may never enter. LGPL needs an `ALLOWLIST` entry *and* a proof that a recipient
  can substitute their own build - which is why `pystray` sits in the spec's `collect_all` loop,
  landing as readable source under `_internal/pystray/`. Since 2026-08-29 `num2words` is proven
  the other way: the bundle does not contain it, `voice_runtime` fetches its wheel from PyPI on
  a press, and `voice_runtime._verify_relinkable` asserts the unpacked copy is source.
  `license_audit.ACQUIRED_AT_FIRST_USE` names which packages are in which case, and
  `tests/test_license_audit.py` fails if an allowlisted package has no proof or two. No espeak-ng in any form, which is a dependency-review rule
  rather than a preference (`development/ROADMAP.md` Phase 10.5 holds the measurements).
  After any dependency change: `uv sync --extra voice-local` then
  `uv run python packaging/license_audit.py --write`, and commit both generated files.
  **And `uv run python packaging/generate_voice_pins.py --write`** if the change touches the
  `voice-local` extra, the `g2p-model` group, or anything they resolve, because the desktop app
  downloads that closure at first use and the pin table is what says which bytes are acceptable.
  There is no desktop pin table any more: `pystray` and `pywebview` became base dependencies on
  2026-08-30, which emptied the `desktop` extra and deleted the closure that used to be acquired
  from it (`desktop_wheels.py`, `generate_desktop_pins.py`, `tests/test_desktop_wheels.py`).
  `pystray` is still LGPL, still walked (base requirements always are) and still proven
  relinkable in the bundle - what changed is that `build_desktop.missing_relinkable_distributions`
  is the preflight that guards it rather than the extras check.
  The one extra, because the closure walk is defined over `DISTRIBUTED_EXTRAS` and
  `--write` reads licenses out of what is actually installed. `voice-local` is
  optional to install and mandatory to build from, and the reason inverted on 2026-08-29:
  the bundle no longer ships that closure, so the extra is what makes
  `verify_bundle_contents`'s proof of its *absence* meaningful, and what lets
  `build_desktop.voice_closure_top_levels()` read the metadata that builds the spec's excludes.
  `build_desktop.verify_build_extras_installed` and the `redeploy_desktop` preflight both refuse
  a build without it rather than silently reshipping 277 MB.
  A second rule, added 2026-08-28 after `swe-mux[voice-local]` turned out to be
  **uninstallable by anyone** for the whole life of 0.1.0
  (`development/DEPENDENCY_AUDIT_2026-08-28.md` § 4): **a `[tool.uv.sources]` entry is a
  property of this repository's resolution and is not carried in a wheel's `Requires-Dist`,
  so nothing that needs one may be a published requirement.** `en-core-web-sm` exists on no
  index and was declared in the extra, so pip and uv both refused the whole extra outright -
  and no gate, no test and no CI job noticed, because nothing anywhere installs an extra from
  an index. It now lives in the `g2p-model` PEP 735 dependency **group**, which a wheel never
  carries, and an installed copy acquires it at first use (`voice_models.SpacyModelStore`).
  Three consequences follow and each is enforced rather than remembered. A group that ships
  has to be named in `license_audit.DISTRIBUTED_GROUPS` *and* in
  `build_desktop.REQUIRED_BUILD_GROUPS`, or the audited closure and the bundle stop describing
  each other. A direct URL is not an escape hatch, because PyPI rejects a distribution whose
  `Requires-Dist` contains one. And `tests/test_dependency_declarations.py` asserts the rule
  from both sides - no published requirement may name a `[tool.uv.sources]` entry, and every
  published requirement must have resolved from a registry in `uv.lock`.
  The same document's § 5 and § 6 are worth reading before removing anything: `tzdata` is
  imported nowhere and must never be removed on that evidence, while `cryptography`,
  `py-vapid`, `numpy`, and `ctranslate2` were imported directly and declared nowhere.
- Changing the project's own license, trademark posture, contribution terms, or how swe-mux
  describes its relationship to the agent vendors: `LICENSE`, `NOTICE`, `TRADEMARK.md`,
  `CONTRIBUTING.md`, `README.md`, `site/index.html`,
  `design/features/provider-accounts.md` ("Scope and terms"),
  `development/ROADMAP.md` Phase 10.5 (the recorded decisions and why MIT, AGPL, and a CLA
  were each rejected). Contributions arrive under a DCO sign-off, not a CLA; that choice is
  what makes relicensing the core impossible later, and it is deliberate.
- Changing reusable prompt templates: `design/features/prompt-library.md`,
  `design/interfaces.md`, `design/data-model.md`
- Changing the prompt queue (message model, states, head-of-line, send-next, stranding,
  Queue tab, send-to-agent senders, new-session seed staging):
  `design/features/prompt-queue.md`, `design/features/delivery-readiness.md`,
  `design/interfaces.md`, `design/data-model.md`, `technical/backend/packages.md`,
  `technical/frontend/packages.md`
- Changing gated auto-delivery (the per-conversation default/override, stability window, quiet hours,
  emergency pause, item scheduling/expiry, the consecutive-send cap and what clears it, mid-turn
  delivery, the reply window and what counts as evidence for it, or the promotion criteria):
  `design/features/auto-delivery.md`, `design/features/prompt-queue.md`,
  `design/features/delivery-readiness.md`, `design/features/agent-messaging.md`,
  `design/features/land-queue.md`,
  `design/interfaces.md`, `design/data-model.md`
- Changing who may stage a queue message **armed** - the Phase 5 floor, `solicited_by`, or
  which non-human senders the auto-delivery controller will act on:
  `design/features/agent-messaging.md`, `design/features/auto-delivery.md`,
  `design/features/land-queue.md`, `design/data-model.md`,
  `technical/backend/packages/agent-surfaces.md`, `technical/backend/sqlite.md`.
  The rule the design turns on: arming is **never** the sender's claim, and there are exactly
  two forms of receiver authorization - the target's standing `accept_agent_messages` grant,
  and a per-message `solicited_by` naming a request the target itself made. The floor ("a
  non-human sender's write ends at a human") is about *unsolicited* writes, so answering a
  request the receiver explicitly made narrows it rather than eroding it - and the narrowing
  is only legitimate while it stays exactly as wide as the request: the requester alone, a
  fixed daemon-authored template, the run that asked, a per-request cap, and off with the
  authority that accepted the request. Arming is still not delivery, and refusing to arm is
  never refusing the message.
- Changing agent-to-agent messages, the fleet queue (the app-wide authorship view, served
  by the older-named `/api/queue/mailbox`), sender provenance, or drafted spawn requests:
  `design/features/agent-messaging.md`, `design/features/mux-mcp.md`,
  `design/features/observations.md`, `design/features/prompt-queue.md`,
  `design/features/ui.md`, `design/interfaces.md`, `design/data-model.md`
- Changing root-session or quota-reset sounds: `design/features/notifications.md`,
  `design/features/voice.md`
- Changing what the desktop bundle carries, or adding a first-use asset it acquires instead:
  `design/features/desktop-shell.md`, `design/features/voice.md`,
  `development/NEW_USER_RELEASE_READINESS.md`,
  `technical/backend/packages/voice-and-assistant.md`, `packaging/license_audit.py`.
  Three rules the split exists to enforce, all of them learned by measuring rather than reading.
  **The pin table is generated from `uv.lock` and never edited**
  (`packaging/generate_voice_pins.py` -> `src/swe_mux/voice_wheels.py`): a hand-maintained
  description of this repository's resolution drifts the first time anybody upgrades, and the
  failure that produces is not a broken build - it is a first-use download of a closure nobody
  audited, which no other gate here would notice.
  **A bundle that stops shipping a closure must still carry what that closure's *imports* need**
  - the whole standard library and, on Windows, `python3.dll` for the `abi3` wheels that link
  against it by name. Excluding a package hides its import graph from PyInstaller's analysis
  without making the graph go away, and both of these were found only by building a frozen probe
  and watching it fail one module at a time.
  **An obligation follows the copy, not the package name.** `num2words` is LGPL; the bundle no
  longer contains it, so `verify_bundle_licenses` proves nothing about it and
  `voice_runtime._verify_relinkable` proves the relink condition on the tree that is unpacked
  instead. `license_audit.ACQUIRED_AT_FIRST_USE` is what keeps the generated notices from
  pointing a reader at `_internal/` for a package that is not there.
- Changing web push, which device a notification reaches, or the notification preference
  shape: `design/features/notifications.md`, `design/features/device-presence.md`,
  `design/interfaces.md`, `technical/backend/packages.md`,
  `technical/frontend/packages.md`
- Changing managed-harness account capture, switching, or quota polling:
  `design/features/provider-accounts.md`, `design/features/backends.md`
- Changing history, transcripts, or cross-vendor review: `design/features/history.md`,
  `design/interfaces.md`
- Changing how a transcript is linearized - which records count as the conversation, what a
  retry or `/rewind` leaves behind, or anything reading `parentUuid`:
  `design/features/transcript-branches.md`, `design/features/history.md`,
  `design/interfaces.md`, `technical/backend/packages.md`.
  The rule the split exists to enforce: a Claude transcript is an append-only DAG, so the
  indexing projection drops the branches the conversation left and the human reader marks
  them - and neither may reconstruct the live branch from the parent chain alone, because a
  parallel tool batch parents each result to its own call and a chain walk would drop every
  result but the last.
- Changing Git status, comparison, diff review, first-time repository initialization, or worktree tooling:
  `design/features/git.md`, `design/features/project-resources.md`, `design/interfaces.md`,
  `technical/backend/packages.md`, `technical/frontend/packages.md`,
  `technical/frontend/workspace-state.md`.
  Two rules worktree *removal* turns on. The deletion is moved off the request - the checkout
  is renamed into `<git-common-dir>/swe-mux-graveyard` and purged in the background - and that
  location is load-bearing rather than tidy: outside every working tree (a buried checkout in
  `git status` would raise dirty counts and make the land queue refuse that checkout), inside
  `.git` so no watcher walks the purge, and never in `git worktree list`. What drops the
  registration is `git worktree remove` on the original path, never `git worktree prune`,
  which is global and would take every other checkout whose directory is merely missing. The
  rename is *declined* wherever it would change what the removal means (locked, submodules,
  unclean without force), so Git's own refusals stay Git's. And the "removing" indication is a
  property of the **list**, never of a row: a row's own state stops when the row is collapsed,
  and the removal's response ends it too early on both paths, so only the refreshed inventory
  no longer listing the checkout ends it.
- Changing the land queue (its pipeline steps, preconditions, the verification gate, its
  approval or its editor, what a running gate reports, the grant, the Land control on a Map
  row, or the landing strip at the head of that map): `design/features/land-queue.md`,
  `design/features/git.md`, `design/features/project-actions.md`,
  `design/features/setting-links.md`, `design/features/ui.md`,
  `design/features/prompt-queue.md`, `design/features/automation-enablement.md`,
  `design/features/mux-mcp.md`, `design/interfaces.md`, `design/data-model.md`,
  `technical/backend/packages.md`, `technical/backend/sqlite.md`,
  `technical/frontend/packages.md`.
  The rule the design turns on: the pipeline executes a *fixed* git vocabulary and never
  decides anything - fast-forward-only is what makes the trunk step safe for a machine,
  because Git refuses it on divergence and refuses to overwrite local changes, so the
  pipeline cannot lose work by construction. A conflict and a failed gate both need
  intelligence and both belong to the branch's own agent, so they leave as a bounded
  deterministic message rather than being resolved here. And the verification command is
  *not* a Project Action: an action's cwd is bounded by the Project root and deliberately
  denied the sibling-worktree widening, so it borrows only the exact-content approval,
  which is what stops an agent approving the command its own land runs - editing the
  command and approving it stay two acts against two routes, and a write can never produce
  an approved command because the approval is a digest over the bytes it just moved.
  Approval stopped being the *only* authority a run can have on 2026-08-29, and the rule
  that replaced it is decided by **who wrote the bytes rather than by what they say**: a
  Project's `land_verify_grant` lets its own agents' gate edits run unapproved, where "its
  own" is `verify_provenance.py` tracing the bytes to this machine, and a gate any other
  author put on the branch still presents for approval. Both halves are required and
  neither is redundant - the switch alone would make landing a contributor branch an
  unattended execution of that branch's script, which is a live risk now the repository is
  public, and the provenance alone would ignore a Project that asked to be asked. The
  bypass is scoped to the run and writes nothing to the approval store, so lowering the
  authority takes the permission away completely rather than leaving auto-granted digests
  standing beside ones a human read; what it owes instead is the trail, which carries the
  verdict and a bounded approved-to-current diff. Every standing readout is therefore drawn
  from `approved || runs_without_approval`, never from `approved` alone.
  And **clearing a block resumes the lands it ended**: a refusal is terminal, so approving
  the bytes used to leave the request that caused the block dead and needing to be asked
  for again. Approving, and raising the authority, both re-queue what they cleared and
  name it in their response. The resumed request is a **new row** naming the refused one
  (`resumed_from`) rather than a revived one - the trail has to go on saying the refusal
  happened, and a terminal row may not gain a second writer - and it skips exactly the two
  checks that decide whether a *new agent* request should start (the per-origin budget and
  the `draft` grant), because a resume is neither new nor the agent's.
  Two consequences for what a terminal row *says*. Its message is **recorded, not only
  sent**: `_solicited_reply` writes to the session that asked and returns early when there
  is none, so an operator's own Land composed its explanation and dropped it, and the one
  requester standing in front of the queue never saw it - the terminal event now carries
  the `body` and the row reads it back with a copy control, the daemon's own words rather
  than a second set written in the browser. And **a later request is not the only thing
  that can answer a branch**: a fast-forward refusal tells its author to merge and land by
  hand, which produces no later request at all, so `absorbed_by_trunk` answers a refusal
  whose tip the trunk already contains. Derived per reading, never written back, for the
  same reason the supersession rule it extends is.
  *Which* gate runs is decided by a `classify` step, and it stays on the executing side of
  the same line: matching paths against a **closed** documentation allowlist is a total
  function with no model, heuristic, or configuration in it, so it is not a decision -
  what would cross the line is judging whether a change "looks risky". Everything it
  cannot answer with certainty answers "the full gate", including a rename between two
  documents; and the classification is recorded on **both** outcomes, with the skipped
  `verify` step still present in the trail and the class persisted on the row, because a
  documentation-only land never enters `verifying` and would otherwise read exactly like
  one that passed three minutes of pytest.
  A *second* way to skip it obeys the same audit rule: a request comes in one of two
  **kinds**, and the kind decides exactly one thing - whether the fast-forward happens.
  A `verify` request runs every earlier step identically, which is what makes its verdict
  the verdict a land would have produced, and that verdict is kept against the git
  **tree** it ran over and the **digest** of the command that ran (the tree, not the
  commit: a reconcile over an unchanged trunk makes a new commit over identical content,
  which is exactly the case a commit key would miss). A later land over the same content
  skips the gate and records the reuse *with its key*, so it is checkable rather than
  asserted; a moved trunk yields a new tree and the gate runs again, which is correct.
  Only a run the queue executed is ever kept - an agent's own shell run is self-reported
  and has a file-swap loophole (run modified bytes, restore the approved file) - and that
  same asymmetry is why a green self-run does not let an agent land itself: the gate is
  the expensive part, not the authoritative one, and landing outside the queue puts a
  second writer on the primary checkout, skips the per-mutation preconditions, and leaves
  the audit and provenance with a hole where that land was.
  A second rule governs what a *running* gate may say: every signal is observed or absent,
  never estimated. A step number counts markers the gate itself printed, a step *total*
  exists only where a byte-identical run has already passed and is withdrawn the moment a
  run overruns it, a line count is stated as evidence of output rather than as progress,
  and no percentage is derived at either end - the steps of this repository's own gate take
  45s and 3s in one run, so a denominator drawn as a proportion would be fiction, and a
  wrong number is acted on where an absent one is not.
  The reading is in memory and dies with the process; only the *plan* is durable, because
  it is a measurement of bytes rather than of a run.
  A third rule governs *where* landing is drawn: it has no surface of its own, and each
  part lives once, decided by what it is a property of. A worktree row owns the act and
  nothing else, because a row is repeated per checkout and a Project-wide fact drawn there
  is drawn N times - which is what the verification block did under every expansion before
  it moved. A compact strip at the head of the map owns the queue, the verification
  command, and the grants; a blocked row *opens* it rather than drawing a second copy of
  its control. Retiring the Land segment did not delete its palette command, its voice
  phrases, a stored selection, or a keybinding - all four migrate onto Map and the rows
  stay forever (`drawerSegments.ts`, `drawerLayout.ts`, `keybindings.py`).
  Two consequences of that strip always speaking while folded. Its headline picks the most interesting
  row, so a bounced request must stop speaking once a *later* request for its branch got
  an answer - nothing closes the old row and the redo is a new id, so without the rule the
  strip reports a branch as returned-to-agent forever; and it is derived at the reading
  rather than written back, because the trail is an audit that must go on saying the
  handback happened. And the verification section's copyable setup prompt for another
  repository ends by telling the receiving agent it cannot approve what it wrote - the
  daemon enforces that regardless, but a prompt that omitted it would send an agent to do
  work whose last step it is not allowed to take, without saying so.
- Changing attention ranking, the interrupt budget, the four delivery channels, breakpoint
  detection, the absence digest, mined demotion rules, or model narration:
  `design/features/attention-ranking.md`, `design/features/deterministic-consumers.md`,
  `design/features/fleet-intelligence.md`, `design/features/automation-enablement.md`,
  `design/interfaces.md`, `design/data-model.md`, `technical/backend/packages.md`,
  `technical/backend/sqlite.md`, `technical/frontend/packages.md`
- Changing automation, observers, attention, or legacy hooks:
  `design/features/automation.md`, `design/features/fleet-intelligence.md`,
  `design/features/meta-hooks.md`, `design/features/delivery-readiness.md`.
  The rule the presentation turns on: the pipeline produces exactly two things - an attention
  item or a run note. Run notes keep exactly one home (Activity → Findings); attention items
  have two surfaces (the Alerts drawer tab and the Automation dashboard's Activity tab), and
  the second is the SAME `AttentionInbox` component over the same endpoints, so mirroring is
  done with components, never copies - the rule `AutomationSpendView` already carries for
  spend (Usage ↔ Automation → Usage). The dashboard is three tabs (Policy | Usage |
  Activity) and its Policy matrix is the ONE editor that may turn an automation off in
  either scope - the install-wide ceiling (`automation_global_allow` + the dedicated
  switches) and every Project opt-in - which is what keeps every grant gate additive-only.
  The install limits live behind the Policy tab's Limits & budgets disclosure; Settings →
  Automation only shows status and links there.
- Changing session status detection, the transition ledger, the state watchdog,
  awaiting sub-reasons, the detection golden corpus, or status-health diagnostics:
  `design/features/status-detection.md`, `design/features/delivery-readiness.md`
- Changing control-plane approvals (the per-conversation mode, the allow rules, the
  never-auto-approved floor, the decision hook, or the approval strip):
  `design/features/approvals.md`, `design/features/status-detection.md`,
  `design/features/backends.md`, `design/interfaces.md`, `design/data-model.md`,
  `technical/backend/packages.md`, `technical/frontend/packages.md`.
  The rule the design exists to enforce: a decision is made from the harness's structured
  permission request, never from the PTY screen, and the floor in `approvals.py` is checked
  before the mode so no configuration can reach past it. `deny` is deliberately not a
  decision mux ever makes.
- Changing the durable status timeline (its table, sink, layer readings, or the
  state-log/diagnostic-bundle endpoints) or the incident investigation procedure:
  `design/features/status-detection.md`, `design/interfaces.md`, `design/data-model.md`,
  `technical/backend/sqlite.md`, `technical/backend/packages.md`,
  `development/STATUS_INCIDENT_RUNBOOK.md`
- Changing background-loop supervision, per-loop cost accounting, event-loop lag sampling,
  or the performance investigation procedure: `development/PERFORMANCE_RUNBOOK.md`,
  `technical/backend/packages.md`, `design/interfaces.md`
- Changing what a `daemon.log` line carries, how a request is correlated across it, or how a
  raised exception becomes an HTTP status: `technical/backend/packages/daemon-runtime.md`
  (`logsetup.py`, `errors.py`, `http_support.py`, the middleware chain), `design/interfaces.md`
  ("Error shapes every route shares").
  Three rules the split exists to enforce. **A field a call site passes must reach the sink**:
  the format string used to drop every `extra=` keyword, so instrumentation written years apart
  produced nothing, and anything added to `LOG_FORMAT` or to a handler's formatter has to keep
  `StructuredFormatter`. **The correlation filter goes on handlers, never on a logger**:
  `Logger.handle` consults only the filters of the logger the call was made on, so one on root
  looks armed and stamps nothing a submodule logs. And **a deliberate refusal and a bug must
  not be the same answer**: `errors.NotFound` is the 404, a bare `KeyError` or `TypeError` is a
  500 with a traceback, and a 404 body names the *kind* of thing that was missing and never
  echoes the key the caller sent.
- Changing what the daemon does before it can serve - adding a startup phase, moving one
  behind the listener, or changing what health says while the runtime is being built:
  `development/PERFORMANCE_RUNBOOK.md` (§Startup latency), `design/architecture.md`
  (invariant 15), `design/interfaces.md`, `design/features/desktop-shell.md`,
  `technical/backend/packages/daemon-runtime.md`, `technical/backend/sqlite.md`.
  Two rules the split exists to enforce. **A bound listener is not a ready daemon**: health
  answers 503 with the phase in flight until the runtime exists, and every consumer decides on
  readiness rather than reachability - a 200 during the build would have the tray, the redeploy
  wait, and the browser's post-restart reload each declare a daemon usable that cannot answer a
  single request. And **nothing may run unlogged for minutes**: a phase is named and timed *and*
  reported while it is still running, because both silent stretches of the 226.6s start this was
  built for were work in flight, which a completion line would never have shown. Anything that
  blocks the event loop on this path defeats both, so it goes in a thread.
  Measure before moving a phase: the obvious suspect (a 2.73 GB `mux.db`) was innocent and the
  real cost was a per-store integrity probe nobody had timed.
  A new phase needs no test edit and gets no free pass: the `live_daemon` tier
  (`tests/test_live_daemon.py`, run in CI on Linux and Windows) derives the expected phase
  list from `server.py` with an AST walk over the unconditional `timeline.mark(...)` calls and
  fails when one of them never completes on a clean start. A `mark` you nest inside an `if` is
  outside that derivation by design; an unnamed stretch of half a second or more fails it,
  which is the rule this module exists to enforce.
- Changing HTTP/WebSocket traffic accounting, response compression, static precompression,
  or browser polling cadence: `design/features/remote-access.md`, `design/interfaces.md`,
  `design/features/processes-and-previews.md`, `development/PERFORMANCE_RUNBOOK.md`,
  `technical/backend/packages.md`, `technical/frontend/packages.md`
- Changing scheduled runs (the triggers and their wall-clock/DST arithmetic, the missed-window
  policy, the fire guards, the resume action and its target kinds, the Schedule drawer tab, or
  where a definition is stored):
  `design/features/scheduled-runs.md`, `design/features/automation-enablement.md`,
  `design/features/prompt-queue.md`, `design/features/history.md`, `design/features/ui.md`,
  `design/interfaces.md`, `design/data-model.md`, `technical/backend/packages.md`,
  `technical/backend/sqlite.md`, `technical/frontend/packages.md`.
  The rule the design turns on: a schedule is a *user-authored deferred press of a button the
  author could have pressed themselves*, so it goes through the ordinary spawn path, the
  ordinary resume path (`session_resume.py`, shared with the History Resume button) and the
  ordinary prompt queue, and never grows a second authority; a resume names its conversation by
  history *run* id rather than session id, because a session is exactly the thing that drifts;
  and the definitions stay machine-local, because a schedule committed to a repository would
  arm itself in every clone and worktree.
- Changing per-project automation opt-in, the enablement dependency graph, or its toggle
  surface: `design/features/automation-enablement.md`,
  `design/features/project-resources.md`, `design/data-model.md`, `design/interfaces.md`
- Changing the model-free control-plane detectors (loop/stall, declared-vs-verified,
  doc debt, provenance) or the annotation anchor/evidence schema:
  `design/features/deterministic-consumers.md`, `design/features/tier0-facts.md`,
  `design/data-model.md`, `technical/backend/packages.md`
- Changing Tier 0 deterministic fact capture or its source pointers/fingerprints:
  `design/features/tier0-facts.md`, `design/data-model.md`,
  `technical/backend/packages.md`, `technical/backend/sqlite.md`
- Changing the code-structure graph (tree-sitter engine, import resolution, the
  graph tables, blast-radius/navigation/context/test-gap MCP tools, the
  blast-radius/dead-code/god-node/import-cycle annotations, the doc-debt reach
  refinement, or the per-session change map): `design/features/code-graph.md`,
  `design/features/mux-mcp.md`, `design/features/deterministic-consumers.md`,
  `design/features/automation-enablement.md`, `design/features/desktop-shell.md`
  (grammar bundling), `design/interfaces.md`
- Changing the user-owned Project context card (its fixed file, editor, bounds, revision contract, setup prompt, or scan prefix): `design/features/project-card.md`, `design/features/automation-enablement.md`, `design/data-model.md`, `design/interfaces.md`, `technical/backend/packages.md`, `technical/frontend/packages.md`
- Changing the scan timeline, per-run scan grant, rollover boundary, scan budgets, source
  rehydration, dead-end extraction, or the agent-readable scan surface
  (`scan_timeline`/`scan_search`, the record projection, the digest bounds):
  `design/features/scan-timeline.md`, `design/features/mux-mcp.md`,
  `design/features/automation-enablement.md`, `design/features/automation.md`,
  `design/data-model.md`, `design/interfaces.md`, `technical/backend/packages.md`,
  `technical/backend/sqlite.md`, `technical/frontend/packages.md`.
  Two rules the split exists to enforce: `ScanTimelineService.liveness()` is the single owner
  of the enablement/liveness block, because a scanner stopped by a budget cap and a quiet
  session both return an empty tail and two implementations would eventually disagree about
  which one you are looking at; and no scan or backfill trigger is reachable from MCP, because
  a read costs nothing while a scan spends the human's gated budget.
- Changing the agent MCP surface (endpoint, tools, per-session tokens, CLI registration):
  `design/features/mux-mcp.md`, `design/interfaces.md`, `technical/backend/packages.md`
- Changing session-settle watches (`watch_session`, the fire rules, the settle hold, the
  notice template, or the per-watcher bounds): `design/features/mux-mcp.md`,
  `design/features/prompt-queue.md`, `design/features/status-detection.md`,
  `design/interfaces.md`, `design/data-model.md`,
  `technical/backend/packages/agent-surfaces.md`.
  The rule the design turns on: a watch is a **read that matures into exactly one bounded
  message**, addressed to the session that armed it, so it needs no grant and no opt-in -
  and the moment a watch could address a third session, or act instead of report, it stops
  being that and needs the authority model interrupt/end already have. Two consequences are
  load-bearing and neither is optional: the timeout always fires, because a watch that
  quietly evaporates is indistinguishable from a worker that hung; and `idle` alone is
  never reported as finished, because `awaiting` and idle-with-running-background-work are
  the two states that render identically and mean the opposite.
- Changing the observation inbox: `design/features/observations.md`, `design/interfaces.md`,
  `design/data-model.md`
- Changing preview screenshot capture or the region selector:
  `design/features/processes-and-previews.md`, `design/interfaces.md`
- Changing processes, listeners, Preview ownership/proxying, or Preview tab lifetime:
  `design/features/processes-and-previews.md`, `design/features/remote-access.md`,
  `technical/backend/packages.md`, `technical/frontend/workspace-state.md`
- Changing static document previews (serving a page from the checkout, its entry allowlist,
  its served directory, or where it is launched from): the same four, plus
  `design/features/project-resources.md` for the file-browser and file-tab entry points and
  `design/interfaces.md` for the `POST /previews` static body.
  The rule it turns on: a static preview is a second *kind* on the one registry, never a
  second viewer - so anything that reads a registration must branch on `kind` and not on
  whether a session id happens to be empty.
- Changing headless-browser ghost-window detection, the sweep predicate, or its remediation:
  `design/features/ghost-windows.md`, `technical/backend/packages.md`
- Changing durable process/quota/reset/compaction/tool evidence or retention:
  `design/features/operational-telemetry.md`, `design/data-model.md`, `design/interfaces.md`
- Changing launch profiles (shell or agent), profile resolution, or reserved harness argv: `design/features/launch-profiles.md`
- Evaluating or changing host-OS support, Windows shell compatibility, WSL behavior,
  platform packaging, or the external compatibility matrix:
  `development/CROSS_PLATFORM_FINDINGS.md`, `design/features/launch-profiles.md`,
  `design/features/backends.md`, `design/features/project-actions.md`,
  `development/ROADMAP.md`
- Changing anything behind a platform seam - pseudoterminal allocation, process-tree
  ownership, path identity, where secrets rest, or where the data directory lives:
  `technical/backend/packages.md` (the per-module map), `development/ROADMAP.md` Phase 10.
  The rule the seams exist to enforce: `host_platform.py` answers *which host this is* and
  nothing else, while whether a capability exists is answered by the module that owns it -
  a capability can be absent on a supported platform, and conflating the two is how a port
  starts claiming parity it does not have.
  Verify a change on both hosts, not one: `tools/linux_container_verify.sh` runs the suite
  on Linux from a Windows host with only Docker, and `.worktree-verify` runs the two
  `--platform` mypy passes so each host's implementation is typechecked wherever the gate runs.
- Changing the WSL agent bridge (distro-side discovery, path translation, the bridge script,
  the WSL listener, or its firewall rule): `src/swe_mux/wsl_bridge.py`,
  `design/features/backends.md`, `development/ROADMAP.md` Phase 10.
  Its failure mode is silence by construction - a bridged agent that cannot reach the daemon
  runs perfectly and simply never reports - so any change must keep the reachability probe
  and the `reasons` it produces, and must never let "not checked" render as available.
- Changing a spending cap, adding one, or changing what a cap is denominated in:
  `design/features/budgets.md`, plus the owning feature's doc
  (`design/features/automation.md`, `design/features/scan-timeline.md`,
  `design/features/assistant.md`, `design/features/voice.md`,
  `design/features/attention-ranking.md`, `design/features/project-card.md`),
  `design/data-model.md`, `design/interfaces.md`, `design/features/setting-links.md`.
  Two rules the split exists to enforce. Every cap is `{tokens?, usd?, mode}` edited through one
  control, and migration maps a pre-existing cap onto the mode matching the unit it already
  enforced - so a config written by the previous build enforces exactly what it enforced before,
  and adding a cap without giving it the choice is the regression to watch for. And a dollar cap
  cannot bind against a provider that reports no cost: absent cost is unknown, never zero, so the
  ledger records it as unmeasured (`cost_known`), every total drawn over it reads as a floor, and
  the token axis is the honest backstop. Rate limits and per-call ceilings are deliberately *not*
  budgets: they count acts and bound one request, never a period's spend.
- Changing usage analytics: `design/features/usage.md`
- Changing read aloud or hands-free conversation: `design/features/voice.md`;
  completed voice-interaction phases and their decisions:
  `development/archive/VOICE_INTERACTION_ROADMAP.md`
- Changing a TTS provider, provider package boundary, voice discovery, or synthesis output:
  `design/features/voice.md`, `design/interfaces.md`,
  `technical/backend/packages/voice-and-assistant.md`,
  `technical/frontend/packages/voice-and-assistant.md`, `design/features/desktop-shell.md`, and
  the dependency/license routing entry in this file.
- Changing the Mux assistant (the chat surface, the tool bridge, the trust policy, the
  voice fallback tiers, or its dialogs/actions): `design/features/assistant.md`,
  `design/features/voice.md`, `design/interfaces.md`, `technical/backend/packages.md`,
  `technical/frontend/packages.md`; the plan: `development/ROADMAP.md` Phase 10.6.
  The rule the design enforces: the model proposes names, deterministic code resolves and
  executes through existing paths, and the consequential-action confirmation floor is not
  configurable.
  A second rule governs what is *said*: a turn is one speech stream fed progressively as the model writes and batched into natural audio segments by the daemon,
  as the model writes it, a confirmation card is announced once by the daemon-built line and
  everything the model says afterwards is display-only, and an identical proposal is answered
  with the existing action rather than a second card - because a confirmation is never a turn,
  so nothing in the message log records that the operator already said yes.
  A third rule governs what a turn can *get done*: it has a round budget it is told about and
  asked to batch against, running out of rounds is announced rather than silent, and anything
  the operator says while a turn runs is queued (and merged with the next fragment) rather than
  refused - a refusal had nowhere to go and simply lost what they said.
  A card is announced **once per card**, never per event, and its cancel window moves **once**:
  extending re-emits the card and a device announces a card when it sees one, so a second
  extension closes that into a loop that talks over the operator with no way to stop it
  (2026-08-20). Announcing joins the stream already speaking; only a spoken verdict interrupts.
- Changing listeners, Tailscale, browser security, or remote operation:
  `design/features/remote-access.md`
- Planning remaining work: `development/ROADMAP.md`; control-plane plan + completion
  checklist (start at §9): `development/CONTROL_PLANE_ROADMAP.md`; the completed
  audit-remediation programme and the decisions its twelve packages recorded:
  `development/archive/ROADMAP_V2.md` (source findings:
  `development/CODE_QUALITY_AUDIT_2026-08-23.md`).
  Two things there are still forward-looking rather than history: § S12 holds the C901
  step-down plan that `pyproject.toml` and the root `CLAUDE.md` both point at, and § S12.2
  names `component-jsx` and `composition-root` as the source-text test debt a renderer
  harness is the way off.
- Changing launch marketing material - blog/post drafts, the positioning line, or the venue
  checklist: `marketing/README.md` (index + rules) and `development/ROADMAP.md` Phase 11.
  The rule the drafts carry: every claim must be true of the shipped artifact on the day it
  posts, `[verify]` markers are re-measured before publishing, and the positioning line is
  one string used verbatim everywhere.
- Changing what an **installed** copy looks like from the operator's side - an install, upgrade,
  or uninstall path; what an uninstall leaves behind; where the data directory or any file under
  it lives; what `swemux doctor` answers in either mode, including its exit codes; a recovery
  procedure for a daemon that will not start, sessions that look lost, or a missing frontend
  bundle; or what is worth backing up: `development/OPERATOR_LIFECYCLE.md`, plus the source of
  the fact you changed - `src/swe_mux/config.py` (`default_data_dir`, the data-dir children),
  `src/swe_mux/doctor.py` and `src/swe_mux/doctor_local.py` (the two reports),
  `src/swe_mux/install_location.py` (which install shape this is, which launchers it shipped,
  and - since the console client became its own frozen bundle - which of the sibling bundles
  under one `{app}` is running), `src/swe_mux/cli.py` (the exit-code contract),
  `packaging/installer/swe-mux.iss` (what the Windows installer writes, including the one
  `PATH` entry it adds and takes back), `pyproject.toml` (extras, entry points,
  `requires-python`), `README.md`, `RELEASING.md`, `SECURITY.md`.
  Two rules that document carries. **Every command, path, flag, and exit code in it is verified
  against the repository rather than plausible** - it is read by someone whose install is already
  broken, so a command that does not exist costs them the one thing they came for. Since 2026-08-28
  that bar is higher for install commands specifically: `swe-mux` 0.1.0 is on PyPI, so a `uv tool`,
  `pipx`, or `pip` command is *runnable* and is therefore expected to have been **run** into a
  throwaway environment before it is written down, not derived from `pyproject.toml` metadata.
  And **it states what is not yet true rather than describing an intention**: the platform matrix
  is read out of `.github/workflows/ci.yml` rather than out of any prose summary.
  The specific overstatement to watch for is the one the wheel makes easy. CI builds and
  install-smokes the artifact on all three hosts, so "the wheel installs and the CLI runs" is
  proven everywhere - but **no CI job on any host starts a daemon from that artifact**
  (`packaging/install_smoke.py` says so and means it), so nothing may claim a platform is
  verified working end to end. Since 2026-08-28 the `live_daemon` tier does start a daemon on
  `ubuntu-latest` and `windows-latest`, from the **source checkout**, on an ephemeral port under
  a `tmp_path` data dir; it proves startup, a real shell session, and a clean exit, and it
  proves nothing about the wheel, the frozen app, an agent, or the operator's own data
  directory. Windows remains the only platform that proves the product running.
  Since 2026-08-29 the `installer-cycle` job compiles the Windows installer and runs
  install → upgrade → uninstall against a seeded `HKCU\Environment\Path`, so a claim about what
  an install *does to PATH* is now measured on every push
  (`packaging/installer/verify_path_cycle.ps1`). It still starts no daemon and packs stub app
  and supervisor bundles, so it says nothing about the product running - which is exactly the
  distinction this paragraph exists to keep.
  Note that clean-machine validation - a real machine, from the published artifacts, with no
  checkout - is a different act again, needs hardware this repository does not have, and stays with
  the operator (`development/RELEASE_MANUAL_TASKS.md` § 8).
- Changing backend package ownership or shared SQLite behavior:
  `technical/backend/packages.md`, `technical/backend/sqlite.md`

# Audio (voice) system — quick reference

Full detail: `design/features/voice.md`. Two independent halves in one `VoiceService`
(`src/swe_mux/voice.py`):

- **Read aloud (TTS):** `turn_ended` (auto, 1s debounce) or manual → last-turn slice →
  `summary` (OpenRouter cheap model, budgeted under `builtin:voice-summary`) or `verbatim`
  (`speechify`, no LLM) → OS voice (SAPI), local Kokoro-82M, or explicit experimental
  external Edge TTS → WAV/MP3 clips in `<data_dir>/voice/` + `voice_clips` SQLite.
  Edge is never bundled: `edge_tts_provider.py` runs the shipped Apache bridge under an isolated
  managed or operator-supplied Python containing the LGPL client, requires a versioned
  service/privacy acknowledgement before text leaves the machine, and exposes only explicit
  install/probe/catalog-refresh network operations.
  The managed install is user-gesture-gated, `uv`-driven, staged and bridge-verified before its
  atomic activation under `<data_dir>/integrations/edge-tts/current`; its phases are cached status,
  not a request held open.
  One immutable `TtsProfile` owns every segment in a stream, and provider-aware
  `synthesis_key` reuse prevents an old provider, voice, prosody, model, or lexicon from
  satisfying a newly configured request.
  Words the Kokoro repair ladder can only spell out letter by letter are fixable with a
  `tts_kokoro_lexicon` respelling (Settings → Voice; hot-applied), and each spell-out is recorded
  durably (`spelled_words.json`, surfaced as `kokoro_spelled_words` by `GET /api/voice`) with a one-tap respell there.
  A respelling must itself be pronounceable (the editor checks each row via
  `POST /api/voice/lexicon/check` and auditions via `GET /api/voice/lexicon/preview`);
  exact sounds use misaki's `[word](/phonemes/)` form, atomic in the ladder.
  Automatic and manual read-aloud keeps ordinary replies in one coherent clip and returns a complete opening sentence for longer streams before tracked background tasks synthesize the remaining clips.
  Trusted application speech opens around a natural 120-character boundary with a 200-character hard ceiling, then combines later complete sentences up to 420 characters.
  Those segments are **rows, not clips**: `stream_id`/`segment_index`/`segment_count` (schema 3) make one reply one entry everywhere a person looks (`clip_groups`/`group_snapshot`, growing live as its segments land), a completed stream is joined into a single file under a new id with the segments kept servable for ten minutes, eviction and deletion take whole streams, and pre-schema-3 rows are discarded by the migration because they cannot be reassembled.
  The assistant opens an empty acknowledgement-only application stream, appends raw sentence fragments without waiting for synthesis, and lets the daemon batch accumulated text once while the preceding clip encodes; one worker per stream keeps clip indices monotonic, and `voice_stream_closed` marks the end.
  Browser playback uses one active singleton audio element plus one standby preload element; measured handoff diagnostics distinguish synthesis starvation from browser media delay, and confirmed speech hard-stops and suppresses the whole current stream.
  Read aloud is **one policy in three ordered layers**: the `tts_enabled` master (off = nothing generates *or* plays, enforced on the auto path, `generate`, and `speak` alike), per-session `voice_mode` (does *this session* generate), and the device autoplay toggle plus a global focus rule — the focused session plays here and every other session **holds** its clip, surfaced as `▶ n held` on that pane's strip and in the command palette rather than spoken over the operator. Settings → Voice renders the three as one numbered block and owns the master; the voice panel's `tts` tab is the operational surface for layers 2 and 3 and for the global clip list, ordered by the *source message's* arrival (`voice_clips.source_ts`/`message_anchor`) rather than by synthesis time.
  Failures are typed `VoiceError` and never touch the PTY/history/transcripts.
- **Conversation (STT):** browser capture through an `AudioWorklet` → 512-sample 16 kHz frames →
  **Silero VAD** (`sileroVad.ts`, lazy ~11 MB WASM runtime + ~2.3 MB ONNX model assets; energy detector as fallback) → the
  frame-counted endpoint gate (`speechGate.ts`: 352 ms tail, speculative decode at 160 ms) →
  `voice/transcribe` → faster-whisper, **two decode profiles** (`command` = `small.en` greedy,
  `dictation` = `turbo` with beam 5 above 3 s), decoded from memory with no disk write →
  wake-word + command-phrase **suffix**, which a speculative transcript can use to short-circuit the remaining tail → one workspace-level draft targeting the focused Agent or text surface.
  App-owned Talk state renders in the focused pane's floating voice overlay, with a fixed top fallback when no terminal pane is visible.
  It names the target, supports an exact-target pin, keeps the draft across focus changes, and retains a device-local user/Mux history.
  Dynamic navigation, direct spawn, typed fleet/help/reply queries, and guarded approvals resolve through voice aliases on the ordinary command registry.
  With a running session focused, that registry also exposes terminal copy/paste, explicitly voiced safe rail keys, and configured agent skill/slash rail items through `railVoice.ts`.
  It excludes attachments, keyboard mode, composer-clearing keys, arbitrary prompt/text macros, and destructive rail actions.
  Those commands execute through the mounted terminal owner and report its acknowledgement instead of assuming a PTY action succeeded.
  Spoken navigation uses live hierarchical indexes without requiring a prior list: `Project N` follows visible sidebar order, bare `Session N` follows the selected Project's sidebar session order, `next/previous session` traverses that same Project-scoped order without wrapping, and `Project N Session N` resolves both coordinates atomically.
  Spoken lists retain those canonical addresses, speak five explicitly separated items at a time, and keep five-minute device-local paging context for next/repeat/detail follow-ups.
  Playback leaves capture open; possible speech immediately ducks app audio, waits three frames for echo to drain, then three accepted frames on the quiet mic stop the whole stream before transcription and continue as ordinary dictation or wake-word command input. Rejected echo restores playback. Bare `Mux, stop` stops playback without releasing Talk.
  Agent Send and Append use the mounted terminal's acknowledged xterm path, so Send appends to an existing composer, waits 180 ms for bracketed-paste commit, and then uses the same carriage return as the visible mobile control.
  Note, Scratchpad, Markdown, and Queue-composer Send and Append only insert at the caret without staging or delivering a queue item.
  Voice Comms pins one Agent, prepends a short-response protocol once per run, and temporarily enables automatic verbatim playback until restored.
  Capture always decodes through session-free `/api/voice/transcribe`.
  Wake words and the phrase→action map are user-configurable (`voice_wake_words` /
  `voice_commands` in config, editable in Settings → Voice; `buildVoiceMatcher` compiles them).
  Fixed action set: `send`/`append`/`cancel`/`undo`/`mute`/`read`/`summary`/`verbatim`/`interrupt`/
  `help`/`standby`/`resume`/`hold`/`proceed`/`comms_on`/`comms_off`/`stop`. `standby` keeps the mic on but ignores everything except a
  `resume`/`stop` command; `stop` releases the mic. `hold`/`proceed` are the chat-mode
  brainstorm pair: plain speech buffers instead of becoming assistant turns until "go ahead"
  releases it as one consolidated turn (bare exact phrases "hold on"/"go ahead" also work);
  `voice_chat_patience_ms` separately lengthens the chat-addressee endpoint tail while
  wake-worded commands keep short-circuiting it, and an open assistant confirmation card
  suspends it entirely (a closed question is being answered, not composed).
  A completeness heuristic (`utteranceCompleteness.ts`, pure) runs BEFORE a chat turn is
  dispatched: an utterance ending mid-clause on a dangling conjunction, preposition, or article
  earns exactly one adaptive patience extension instead of submitting, and the held fragment
  merges into the next utterance, submits alone when the extension expires, or folds into a
  brainstorm hold. One deferral per utterance, so the wait is bounded structurally; the model is
  never instructed to return nothing; queue-merge stays the safety net; and every deferral is
  reported to `POST /api/voice/deferral-diagnostic` with its trigger token so the
  false-positive rate is measurable before anyone tunes the word lists. Hold `Alt+Shift+Space` for push-to-talk with
  no endpointing. `GET/POST/DELETE /api/voice/stt-latency` is the end-of-speech-to-action stage
  breakdown (also in `daemon.log`), read in Settings → Voice beside the wake-word tester.
  `POST /api/voice/barge-in-diagnostic` validates and logs confirmed/rejected browser sidechain probes.
  A capture frame watchdog (`CaptureFrameWatchdog`, `conversation.ts`) separates a dead capture from a quiet room: no raw blocks for 5 s renders the `stalled` phase (never `listening`), attempts `context.resume()`, and posts a bounded report to `POST /api/voice/capture-diagnostic` (stall = WARNING in `daemon.log`).
  `voiceQueries.ts` adds the non-configurable deterministic query grammar for help, scoped fleet lists/status, numbered navigation, and one-shot summary/verbatim reply reading.
  Routing is three tiers: this deterministic grammar, a conservative fuzzy pass (`voiceFuzzy.ts`), and — only on no-match, only when enabled — the Mux assistant (`design/features/assistant.md`), whose replies render in the voice panel's `chat` mode and speak through the application-speech path.

**Mobile mic needs HTTPS (secure context).** swe-mux runs **Tailscale Serve on 443**
(`https://<device>.ts.net/`) proxying to the daemon's loopback port — auto-started at boot
(`_auto_enable_mobile_voice`), idempotent. Serve is on 443 **not** the swe-mux port because the
daemon binds its port on the tailnet IP directly for the plain-HTTP fallback (same-port Serve
would collide). The phone must resolve the `.ts.net` name over MagicDNS ("Use Tailscale DNS" on;
Android Private DNS off/automatic); the cert is hostname-bound so the raw 100.x IP can't do
HTTPS. If mobile voice breaks, first confirm a daemon is listening on the real config port and
`tailscale serve status` proxies to it. Tailscale/Serve code: `tailscale.py`, `__main__.py`.
