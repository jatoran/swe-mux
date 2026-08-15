import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const source = (name: string) => readFileSync(join(import.meta.dirname, '..', 'src', name), 'utf8')

test('the drawer exposes one Actions tab with three independently collapsible sections', () => {
  const tabs = source('drawerTabs.ts')
  const drawer = source('UtilityDrawer.tsx')
  const actions = source('ActionsTab.tsx')

  assert.ok(tabs.includes("id: 'actions', label: 'Actions'"))
  assert.doesNotMatch(tabs, /id: 'commands'|id: 'prompts'/)
  assert.ok(drawer.includes("case 'actions':"))
  assert.doesNotMatch(drawer, /case 'commands':|case 'prompts':/)
  for (const section of ['quick', 'skills', 'prompts']) {
    assert.ok(actions.includes(`id="${section}"`), `${section} must remain a first-class Actions section`)
  }
  assert.ok(actions.includes('showManage={false}'))
  assert.ok(actions.includes("label: 'Manage'"))
})

test('Configure Actions is a standalone modal reachable from every intended entry point', () => {
  const app = source('App.tsx')
  const settings = source('Settings.tsx')
  const terminal = source('TerminalPane.tsx')
  const actions = source('ActionsTab.tsx')

  assert.ok(app.includes("id: 'actions.configure'"), 'command palette registry needs Configure Actions')
  assert.ok(app.includes('<ActionEditorModal onClose='))
  assert.ok(app.includes("runNamedCommand('actions.configure')"), 'main menu must use the shared command')
  assert.ok(terminal.includes('aria-label="Configure Actions"'), 'the Action rail gear must open the editor')
  assert.ok(actions.includes('run: onConfigureActions'), 'Quick actions must expose Configure')
  assert.doesNotMatch(settings, /RailEditor|commandrail:/)
})

test('custom action creation leads the editor and keeps its checkbox compact', () => {
  const editor = source('RailEditor.tsx')
  const styles = source('style.css')
  const addForm = editor.indexOf('<RailAddForm')
  const layouts = editor.indexOf('<div class={`rail-devices')
  const catalog = editor.indexOf('<section class="rail-catalog"')

  assert.ok(addForm >= 0 && addForm < layouts && layouts < catalog)
  assert.ok(styles.includes('.rail-add-form input:not([type="checkbox"])'))
  assert.ok(styles.includes('width:14px;height:14px'))
})

test('the Action rail can open Actions without replacing the saved Project tab', () => {
  const app = source('App.tsx')
  const rail = source('commandRail.ts')
  const terminal = source('TerminalPane.tsx')
  const drawer = source('UtilityDrawer.tsx')

  assert.ok(rail.includes("id: 'actionsDrawer', type: 'action', action: 'openActions'"))
  assert.ok(terminal.includes("runCommand('drawer.peekActions')"))
  assert.ok(app.includes("id:'drawer.peekActions'"))
  assert.ok(app.includes('presentation={renderedDrawerPresentation}'))
  assert.ok(app.includes('transientTab={transientDrawerTab||undefined}'))
  assert.ok(drawer.includes('mobile || props.transientTab'))
})

test('prompt templates render excerpts inside Actions while the full editor stays modal', () => {
  const prompts = source('PromptsTab.tsx')
  const actions = source('ActionsTab.tsx')
  const app = source('App.tsx')

  assert.ok(prompts.includes('promptTemplateExcerpt(item.body)'))
  assert.ok(actions.includes('<PromptsTab'))
  assert.ok(app.includes('<PromptLibrary project='))
})
