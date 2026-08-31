import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { SETTING_TARGETS, automationSetting, settingTarget, type SettingTargetId } from '../src/settingTargets.ts'
import { settingsTabs, tabForSection } from '../src/settingsTabs.ts'
import { MODEL_ROUTES } from '../src/modelRouting.ts'

const root = join(import.meta.dirname, '..')
const source = (name: string) => readFileSync(join(root, 'src', name), 'utf8')
const registry = readFileSync(join(root, '..', 'src', 'swe_mux', 'automation_registry.py'), 'utf8')

const ids = Object.keys(SETTING_TARGETS) as SettingTargetId[]

// Every one of these is a promise made on a surface the user is standing on: "the switch is
// over here". A control that gets renamed or moved has to fail here rather than sending
// someone to a panel that scrolls nowhere and flashes nothing.
test('every settings target names a control that exists in the panel it points at', () => {
  const settingsSources = [
    source('Settings.tsx'),
    source('SessionRowSettings.tsx'),
    source('SessionTopbarSettings.tsx'),
    source('NotificationPushSettings.tsx'),
    source('ModelRoutingSummary.tsx'),
  ].join('\n')
  for (const id of ids) {
    const target = settingTarget(id)
    if (target.surface !== 'settings' || !target.setting) continue
    // A shared control carries the mark from its own `name` prop, so the literal
    // `data-setting="..."` lives in the component rather than in the panel. Both
    // forms are the same promise - a marked element the reveal can scroll to -
    // and only the second one is greppable from the panel's source.
    const marked = settingsSources.includes(`data-setting="${target.setting}"`)
      || settingsSources.includes(`<BudgetControl name="${target.setting}"`)
      || settingsSources.includes('data-setting={route.key}')
        && MODEL_ROUTES.some(route=>route.key===target.setting)
    assert.ok(
      marked,
      `${id} points at a control the Settings panel does not mark: ${target.setting}`,
    )
  }
})

test('every settings target lands on a real tab', () => {
  for (const id of ids) {
    const target = settingTarget(id)
    if (target.surface !== 'settings') continue
    assert.ok(target.section, `${id} must name a section`)
    const tab = tabForSection(target.section as string)
    assert.ok(settingsTabs.some(entry => entry.id === tab), `${id} resolves to no tab`)
    // `tabForSection` answers General for anything it does not recognise, so a target that
    // lands there without asking for General is a name that has stopped resolving.
    if ((target.section as string).toLowerCase() !== 'general') {
      assert.notEqual(tab, 'general', `${id} names a section that no longer resolves: ${target.section}`)
    }
  }
})

test('the Automation workspace owns global and per-Project automation controls', () => {
  const dashboard = source('AutomationDashboard.tsx')
  const matrix = source('AutomationMatrix.tsx')
  assert.ok(dashboard.includes('<AutomationPolicyView'))
  assert.ok(dashboard.includes('<AutomationPolicyMatrix'))
  assert.ok(matrix.includes('data-setting="automation_enabled"'))
  // The dedicated install switches are marked from the registry payload's
  // `install_switch` field rather than as three literals, so the mark and the
  // switch it names cannot drift apart; the daemon half of the promise is the
  // registry's own DEDICATED_INSTALL_SWITCHES map.
  assert.ok(matrix.includes('data-setting={item.install_switch||undefined}'))
  assert.ok(registry.includes('"scan_timeline": "scan_timeline_enabled"'))
  assert.equal(settingTarget('automation.engine').surface, 'automation')
  assert.equal(settingTarget('automation.scanTimeline').surface, 'automation')
})

test('Project targets name a marked control or a real automation id', () => {
  const manager =
    source('AutomationMatrix.tsx') + source('ProjectsManager.tsx') + source('AutomationAuthority.tsx')
  // The opt-in rows are marked from the registry entry itself, so every implemented
  // automation is addressable without a per-automation attribute in the source.
  assert.ok(manager.includes('data-setting={automationSetting(item.id)}'))
  for (const id of ids) {
    const target = settingTarget(id)
    if (!['project','automation'].includes(target.surface) || !target.setting || !id.startsWith('project.')) continue
    if (target.setting.startsWith('automation:')) {
      const automation = target.setting.slice('automation:'.length)
      assert.ok(
        // The registry wraps its longer entries, so the id can sit on its own line.
        new RegExp(`Automation\\(\\s*"${automation}"`).test(registry),
        `${id} names an automation the daemon's registry does not define: ${automation}`,
      )
      continue
    }
    // The authority fields render from one table rather than as hand-written rows, so
    // their mark is dynamic — `data-setting={row.setting}` in the Projects registry's
    // summary, `data-setting={spec.field}` on the policy matrix's editor, the same shape
    // the automation rows already use. Both halves are asserted: the attribute exists,
    // and this field is one of the values it is fed. The second half is why
    // `AutomationAuthority` writes its `setting:` keys out as literals instead of
    // deriving them from the payload it renders.
    if (manager.includes(`data-setting="${target.setting}"`)) continue
    const marked = manager.includes('data-setting={row.setting}')
      || manager.includes('data-setting={row.field}')
      || manager.includes('data-setting={spec.field}')
    assert.ok(
      marked && manager.includes(`setting: '${target.setting}'`),
      `${id} points at a Project control that is not marked: ${target.setting}`,
    )
  }
})

// Every authority field the daemon parses is a decision someone has to be able to make.
// All four of the originals shipped enforced and unreachable: a line in a committed TOML
// file with no control in any overlay, which made the inert default impossible to discover
// and impossible to change from the app. One arriving the same way fails here.
test('every grantable Project authority field has an owner in the policy matrix', () => {
  // One owner as of 2026-08-29: the Automation policy matrix, which holds the
  // authority rows (AutomationAuthority), the `scan_timeline_auto_enable` row
  // beside the permission it qualifies, and the install-wide default and ceiling
  // that only exist there. The Projects registry is included because it still
  // renders the read-only summary that makes the fields discoverable from where
  // a Project is configured, and either surface naming a field satisfies this.
  const manager =
    source('ProjectsManager.tsx') + source('AutomationMatrix.tsx') + source('AutomationAuthority.tsx')
  const grantsModule = readFileSync(join(root, '..', 'src', 'swe_mux', 'grants.py'), 'utf8')
  // Split on the declaration, not the bare name: the module's own docstring names it too.
  const table = grantsModule.split('GRANTABLE_PROJECT_VALUES: Mapping')[1].split('\n}')[0]
  const fields = [...table.matchAll(/"([a-z_]+)":/g)].map(match => match[1])
  assert.ok(fields.length >= 5, 'expected the daemon to name its grantable Project fields')
  for (const field of fields) {
    const owned = ids.some(id => {
      const target = settingTarget(id)
      return ['project','automation'].includes(target.surface) && target.setting === field
    })
    assert.ok(owned, `${field} can be granted but has no setting target that owns it`)
    assert.ok(
      manager.includes(`setting: '${field}'`) || manager.includes(`data-setting="${field}"`),
      `${field} can be granted but the Projects registry has no control for it`,
    )
  }
})

test('the automation id rule is one rule, used at both ends', () => {
  assert.equal(automationSetting('code_graph'), 'automation:code_graph')
  assert.equal(settingTarget('project.codeGraph').setting, automationSetting('code_graph'))
})

test('every target carries the words a link renders', () => {
  for (const id of ids) {
    const target = settingTarget(id)
    assert.ok(target.label.trim(), `${id} has no label`)
    assert.ok(target.where.trim(), `${id} does not say where it goes`)
  }
})
