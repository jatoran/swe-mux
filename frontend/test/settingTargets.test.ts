import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { SETTING_TARGETS, automationSetting, settingTarget, type SettingTargetId } from '../src/settingTargets.ts'
import { settingsTabs, tabForSection } from '../src/settingsTabs.ts'

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
    source('NotificationPushSettings.tsx'),
  ].join('\n')
  for (const id of ids) {
    const target = settingTarget(id)
    if (target.surface !== 'settings' || !target.setting) continue
    assert.ok(
      settingsSources.includes(`data-setting="${target.setting}"`),
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

test('automation dashboard targets name controls the dashboard marks', () => {
  const dashboard = source('AutomationDashboard.tsx')
  for (const id of ids) {
    const target = settingTarget(id)
    if (target.surface !== 'automation' || !target.setting) continue
    assert.ok(
      dashboard.includes(`data-setting="${target.setting}"`),
      `${id} points at a dashboard control that is not marked: ${target.setting}`,
    )
  }
})

test('project targets name a marked control or a real automation id', () => {
  const manager = source('ProjectsManager.tsx')
  // The opt-in rows are marked from the registry entry itself, so every implemented
  // automation is addressable without a per-automation attribute in the source.
  assert.ok(manager.includes('data-setting={automationSetting(item.id)}'))
  for (const id of ids) {
    const target = settingTarget(id)
    if (target.surface !== 'project' || !target.setting) continue
    if (target.setting.startsWith('automation:')) {
      const automation = target.setting.slice('automation:'.length)
      assert.ok(
        // The registry wraps its longer entries, so the id can sit on its own line.
        new RegExp(`Automation\\(\\s*"${automation}"`).test(registry),
        `${id} names an automation the daemon's registry does not define: ${automation}`,
      )
      continue
    }
    assert.ok(
      manager.includes(`data-setting="${target.setting}"`),
      `${id} points at a Project control that is not marked: ${target.setting}`,
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
