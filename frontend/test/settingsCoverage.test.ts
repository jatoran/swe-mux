import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { MODEL_ROUTES } from '../src/modelRouting.ts'
import { SETTINGS_CONFIG_FIXTURE } from './renderer/settingsConfigFixture.ts'

/**
 * The other direction of `settingTargets.test.ts`.
 *
 * That test walks from a *link* to the control it promises, which catches a control
 * that is renamed or moved. It says nothing about a field that never had a control in
 * the first place, and that is the gap thirty settings accumulated in: each one shipped
 * enforced by the daemon, reachable only by hand-editing `~/.mux/config.toml`, and
 * nothing anywhere noticed. Several of them were load-bearing - what an agent may do to
 * another session, how long the land queue holds a busy worktree, what a fresh attach
 * replays - and two places in the app told the reader to go find a control that did not
 * exist.
 *
 * So this walks the other way: from every field the daemon's `Config` dataclass declares,
 * to a control in the Settings or Automation policy UI, or to an explicit line below saying why there is
 * none. Adding a field to `config.py` and forgetting the control now fails here, and the
 * escape hatch costs one sentence of justification rather than silence.
 */

const root = join(import.meta.dirname, '..')
const source = (name: string) => readFileSync(join(root, 'src', name), 'utf8')
const configPy = readFileSync(join(root, '..', 'src', 'swe_mux', 'config.py'), 'utf8')

/** Every field name the `Config` dataclass declares, in declaration order. */
export const configFields = (): string[] => {
  const body = configPy.split('class Config:')[1].split('\n    def __post_init__')[0]
  const fields = [...body.matchAll(/^ {4}([a-z_][a-z0-9_]*)\s*:/gm)].map(match => match[1])
  assert.ok(fields.length > 150, 'failed to parse the Config dataclass')
  return fields
}

/**
 * Every panel a Settings control may live in. The panel is one enormous component plus a
 * handful of child sections it composes, and a control is equally owned wherever it sits.
 */
const settingsSources = [
  'Settings.tsx', 'NotificationPushSettings.tsx', 'SessionRowSettings.tsx',
  'ProviderAccounts.tsx', 'remoteConnection.tsx', 'WslBridgePanel.tsx',
  'EdgeTtsSettings.tsx', 'AutomationPolicyView.tsx',
  // The policy matrix owns the install-wide automation switches: the master
  // `automation_enabled`, the three dedicated per-row switches, and the
  // `automation_global_allow` ceiling map its Global column writes.
  'AutomationMatrix.tsx',
  // The seven model settings live here now rather than beside the features they
  // configure, because switching endpoint changes all of them at once.
  'ModelRoutingSummary.tsx',
].map(source).join('\n')

/**
 * Controls edited through something other than the panel's own `change(key, value)`
 * draft setter, keyed by the fragment of source that proves the editor exists.
 *
 * Kept explicit rather than matched by a loose "the field name appears somewhere"
 * rule: reading a field to render a label is not editing it, and a rule that could
 * not tell those apart would pass every field on the allowlist below too.
 */
const BESPOKE_CONTROLS: Record<string, string> = {
  // A per-harness dictionary edited one harness at a time, so no single `change` call
  // names the field with a literal key.
  harness_args: 'setHarnessArgs(current=>',
  harness_mcp_enabled: "setHarnessDict('harness_mcp_enabled'",
  harness_instrument_enabled: "setHarnessDict('harness_instrument_enabled'",
  // Written straight through rather than into the draft: one switch with its own
  // explanation, whose neighbours act on state the daemon must already hold.
  wsl_bridge_enabled: '{wsl_bridge_enabled:enabled}',
  // Previewed live as it is chosen, so it goes through a wrapper that also repaints.
  ui_scale_desktop: "changeUiScale('ui_scale_desktop'",
  ui_scale_mobile: "changeUiScale('ui_scale_mobile'",
  // The three dedicated install switches are the matrix's Global cells for
  // their rows. Which key a cell writes comes from the registry payload's
  // `install_switch` field rather than a literal in the source, so the
  // `data-setting` mark is emitted dynamically and this fragment - the write
  // that flips whichever switch the row names - is what proves the editor.
  scan_timeline_enabled: 'patchConfig({[item.install_switch]',
  scheduled_runs_enabled: 'patchConfig({[item.install_switch]',
  land_queue_enabled: 'patchConfig({[item.install_switch]',
}

/**
 * Fields with no Settings control, and the reason each one has none.
 *
 * This is the whole escape hatch, and it is deliberately a closed list with prose: an
 * entry here is a claim someone made on purpose, which is the difference between a
 * decision and the silence this test replaced. A reason that is only "nobody got to it"
 * is not a reason - wire the control instead.
 */
const CONFIG_ONLY: Record<string, string> = {
  schema_version: 'Migration bookkeeping the loader writes; not a choice anyone makes.',
  revision: 'The optimistic-concurrency stamp every config write carries.',
  config_path: 'Where this config was loaded from - a runtime fact, and not even served to the browser.',
  data_dir: "The daemon's own home. Moving it strands every existing install, so it is MUX_DATA_DIR or a config-file edit.",
  host: 'The bind address. Editing it from a browser connected through it would strand the session that changed it.',
  port: 'The bind port, for the same reason as the host above.',
  shell_exe: 'Superseded by launch profiles, which is where a terminal names its executable; kept for configs that still set it.',
  pty_supervisor_enabled: 'Whether live sessions outlive the daemon. Flipping it reaps or strands PTYs, so it is a deliberate config edit plus a restart.',
  session_recovery_enabled: 'The recovery store is built once at daemon start; switching it off with sessions tracked leaves their rows open forever. Its bounds are on Terminals - Scrollback.',
  harness_setup_complete: 'Whether the first-run harness panel has been dismissed. Written by that panel, read by nothing else.',
  notes_default_open: 'Retired by workspace layout v6 and stripped from public_dict, so the browser never receives it.',
  pinned_directories: 'Written by the directory picker as you pin a folder, which is the control.',
  observer_titler_enabled: "Owned by the Automation dashboard's built-in rule rows, which is where a rule is enabled or disabled.",
  attention_observers_enabled: 'Same: the dashboard enables the attention observer group as one rule.',
}

/**
 * The seven model settings, which are rendered from a list rather than written out.
 *
 * `ModelRoutingSummary` loops `MODEL_ROUTES` and emits one picker per entry, so none
 * of their keys appears literally in any source this file reads - string matching
 * cannot see them however many files it scans. Membership of `MODEL_ROUTES` is the
 * control, and it is not a weaker check than the others here: a new model setting
 * still has to be added to that list to count, and `modelRouting.test.ts` separately
 * refuses a model key the panel knows about that the list has missed.
 */
const ROUTED_MODEL_KEYS = new Set<string>(MODEL_ROUTES.map(route => route.key))

const hasControl = (field: string): boolean =>
  settingsSources.includes(`change('${field}'`)
  || settingsSources.includes(`<BudgetControl name="${field}"`)
  || settingsSources.includes(`data-setting="${field}"`)
  || ROUTED_MODEL_KEYS.has(field)
  || (field in BESPOKE_CONTROLS && settingsSources.includes(BESPOKE_CONTROLS[field]))

test('every Config field has a Settings control or an explicit reason it has none', () => {
  const uncovered: string[] = []
  for (const field of configFields()) {
    if (hasControl(field) || field in CONFIG_ONLY) continue
    uncovered.push(field)
  }
  assert.deepEqual(uncovered, [],
    `these fields are enforced by the daemon and reachable only by editing config.toml.\n`
    + `Give each one a control, or add it to CONFIG_ONLY with the reason it has none.`)
})

test('the config-only list names real fields and never one that has a control', () => {
  // Both halves rot in opposite directions. A stale entry is a field that was renamed
  // or deleted, and it silently widens the escape hatch; an entry that *does* have a
  // control is worse, because the prose beside it now asserts something untrue.
  const fields = new Set(configFields())
  for (const [field, reason] of Object.entries(CONFIG_ONLY)) {
    assert.ok(fields.has(field), `${field} is on the config-only list but is not a Config field`)
    assert.ok(reason.trim().length > 20, `${field} needs a real reason, not a placeholder`)
    assert.ok(!hasControl(field), `${field} has a control now, so it is no longer config-only`)
  }
})

test('every bespoke control still names source that exists', () => {
  // These are the six fields edited by a hand-written handler rather than the draft
  // setter. The fragment is what proves the editor is there, so a refactor that renames
  // one has to come back here rather than quietly turning the field into a pass.
  const fields = new Set(configFields())
  for (const [field, fragment] of Object.entries(BESPOKE_CONTROLS)) {
    assert.ok(fields.has(field), `${field} has a bespoke control but is not a Config field`)
    assert.ok(settingsSources.includes(fragment),
      `${field}'s editor is gone or renamed: no source matches ${fragment}`)
  }
})

test('the renderer fixture serves every field the daemon would', () => {
  // `settingsConfigFixture.ts` is the Settings harness's whole config, and a field
  // missing from it renders as `undefined` in a control that has no way to say so.
  // It had already drifted four fields behind `config.py` by the time this was written.
  // `public_dict` drops exactly two of the dataclass's fields and adds four derived
  // ones, so the fixture's key set is checkable rather than approximately right.
  const served = configFields().filter(field => !['config_path', 'notes_default_open'].includes(field))
  const fixture = new Set(Object.keys(SETTINGS_CONFIG_FIXTURE))
  const missing = served.filter(field => !fixture.has(field))
  assert.deepEqual(missing, [], 'regenerate the fixture from Config(...).public_dict()')
  for (const derived of ['access_mode', 'requires_auth', 'xterm_scrollback_lines', 'pty_windows']) {
    assert.ok(fixture.has(derived), `the fixture is missing the derived field ${derived}`)
  }
  for (const dropped of ['config_path', 'notes_default_open']) {
    assert.ok(!fixture.has(dropped), `${dropped} is stripped by public_dict and must not be in the fixture`)
  }
})
