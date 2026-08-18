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
  const inline = source('RailInlineEditor.tsx')

  assert.ok(app.includes("id: 'actions.configure'"), 'command palette registry needs Configure Actions')
  assert.ok(app.includes('<ActionEditorModal onClose='))
  assert.ok(app.includes("runNamedCommand('actions.configure')"), 'main menu must use the shared command')
  // The rail gear opens the in-place editor; the full modal stays one click away
  // behind its "All options…" control, so a rail configured down to nothing
  // still has a way back into configuration.
  assert.ok(terminal.includes('aria-label="Customize actions"'), 'the Action rail gear must open the in-place editor')
  assert.ok(terminal.includes('<RailInlineEditor'), 'the gear flips the rail area into RailInlineEditor')
  assert.ok(inline.includes('onOpenFull'), 'the in-place editor must reach the full modal')
  assert.ok(actions.includes('run: onConfigureActions'), 'Quick actions must expose Configure')
  assert.doesNotMatch(settings, /RailEditor|commandrail:/)
})

test('the editor discloses progressively: layouts first, creation and catalog collapsed', () => {
  const editor = source('RailEditor.tsx')
  const styles = source('style.css')
  const layouts = editor.indexOf('renderSurface(surface)')
  const addForm = editor.indexOf('<RailAddForm')
  const catalog = editor.indexOf('<details class="rail-catalog"')

  assert.ok(layouts >= 0 && layouts < addForm && addForm < catalog,
    'one device’s layouts lead; creation and the catalog follow')
  // Both heavy sections are collapsed <details>, never forced open.
  assert.ok(editor.includes('<details class="rail-add">'))
  assert.doesNotMatch(editor, /<details class="rail-add" open|<details class="rail-catalog" open/)
  // The catalog’s controls are labelled checkboxes, not the retired badge code.
  assert.ok(editor.includes('Where it appears'))
  assert.ok(editor.includes('Shown in these sessions'))
  assert.doesNotMatch(editor, /rail-where|rail-tags/)
  // One device at a time, defaulting to the device this browser is.
  assert.ok(editor.includes('useState<RailDevice>(() => currentProfile())'))
  // The first-open orientation is dismissible, not a standing paragraph.
  assert.ok(editor.includes('rail-intro-callout'))
  assert.ok(styles.includes('.rail-add-form input:not([type="checkbox"])'))
  assert.ok(styles.includes('width:14px;height:14px'))
})

test('skills and prompt templates can be pinned into Actions in one tap', () => {
  const actions = source('ActionsTab.tsx')
  const prompts = source('PromptsTab.tsx')
  const scope = source('railScope.ts')

  assert.ok(actions.includes('togglePinSkill'), 'the Skills list must carry a pin toggle')
  assert.ok(actions.includes('pin={promptPin}'), 'the embedded template list must carry a pin toggle')
  assert.ok(prompts.includes('pin.toggle(item)'))
  // Pinning goes through the scoped ops so a project-scoped source stays project state.
  assert.ok(scope.includes('export function pinSkill'))
  assert.ok(scope.includes('export function pinPrompt'))
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
