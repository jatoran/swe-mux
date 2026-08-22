import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const source = (name: string) => readFileSync(join(import.meta.dirname, '..', 'src', name), 'utf8')

test('the drawer exposes one Actions tab with three independently collapsible sections', () => {
  const tabs = source('drawerTabs.ts')
  const drawer = source('UtilityDrawer.tsx')
  const actions = source('ActionsTab.tsx')
  const modal = source('ActionEditorModal.tsx')

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
  const modal = source('ActionEditorModal.tsx')

  assert.ok(app.includes("id: 'actions.configure'"), 'command palette registry needs Configure Actions')
  // Project context is passed so detached projects can open directly and Global can offer
  // a one-step detach for projects that still follow it.
  assert.ok(app.includes('<ActionEditorModal projectId='))
  assert.ok(modal.includes("railProjectScopeKind(blob, projectId) === 'fork' ? projectId : ''"))
  assert.ok(modal.includes('contextProjectId={projectId}'))
  assert.ok(app.includes("runNamedCommand('actions.configure')"), 'main menu must use the shared command')
  // Every row's drawer popover reaches the full modal directly, including empty rows.
  const strip = source('RailStrip.tsx')
  const popover = source('RailOverflowPopover.tsx')
  assert.ok(!strip.includes('class="rail-config"'), 'the strip must not draw its own gear')
  assert.ok(strip.includes('onConfigure={onConfigure}'), 'the row must hand Configure to its popover')
  assert.ok(popover.includes('aria-label="Configure Actions"'), 'the overflow popover must reach the full modal')
  assert.ok(terminal.includes('onConfigure={()=>onConfigureRail?.()}'))
  assert.doesNotMatch(terminal, /RailInlineEditor|railEditOpen/)
  assert.ok(source('RailEditor.tsx').includes('Detach {contextProjectName} to edit directly'))
  assert.ok(actions.includes('run: onConfigureActions'), 'Quick actions must expose Configure')
  assert.doesNotMatch(settings, /RailEditor|commandrail:/)
})

// The rail's three pickers are one pattern, and a fourth surface would be a fourth
// answer to "where do I find my prompts". Each opens a drop-up over the rail whose
// sticky row lands on the drawer section it summarises, and each is filtered out of
// that drawer's own grid so it never offers a round trip to where the reader stands.
test('Prompts joins Clip and Skills as a rail picker, not as a fourth surface', () => {
  const rail = source('commandRail.ts')
  const terminal = source('TerminalPane.tsx')
  const actions = source('ActionsTab.tsx')
  const dropup = source('PromptsDropup.tsx')

  assert.ok(rail.includes("id: 'prompts', type: 'action', action: 'prompts'"), 'Prompts must be a placeable built-in')
  // Not agent-only: a template is text, and text suits a shell composer too.
  assert.doesNotMatch(rail, /id: 'prompts', type: 'action', action: 'prompts'[^\n]*agentOnly/)
  assert.ok(terminal.includes('<PromptsDropup'), 'the rail button opens the drop-up')
  assert.ok(dropup.includes("runCommand") === false, 'the drop-up takes its exits as props, not as commands')
  assert.ok(terminal.includes("runCommand('drawer.actions.prompts')"), 'its sticky row lands on the drawer section')
  assert.ok(terminal.includes("runCommand('prompts.new')"), 'its second exit opens the library on a blank template')
  assert.ok(actions.includes("'prompts', 'openActions'"), 'a shortcut to this drawer must not render inside it')
})

test('a new template opens the library already in create mode', () => {
  const app = source('App.tsx')
  const library = source('PromptLibrary.tsx')
  assert.ok(app.includes("id:'prompts.new'"), 'the command must exist for the palette and the drop-up alike')
  assert.ok(library.includes('startCreating'), 'the modal needs a start-in-create prop; creating is internal state')
  assert.ok(library.includes('useState(!!startCreating)'))
  // A property of the *opening*, so a later ordinary open does not inherit it.
  assert.ok(app.includes('setPromptLibraryCreating(false)'))
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
  assert.ok(editor.includes('Button appearance'))
  assert.ok(editor.includes('Visible label'))
  assert.ok(editor.includes('<RailItemIcon'))
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

test('templates are authored where they are used, through one shared form', () => {
  const prompts = source('PromptsTab.tsx')
  const library = source('PromptLibrary.tsx')
  const editor = source('PromptTemplateEditor.tsx')

  // Both hosts drive the same hook and render the same fields; neither owns a
  // second copy of the form that could drift from the other.
  for (const host of [prompts, library]) {
    assert.ok(host.includes('usePromptDraft('), 'both hosts must drive the shared draft state')
    assert.ok(host.includes('<PromptDraftFields'), 'both hosts must render the shared fields')
    assert.ok(host.includes('<PromptDraftActions'), 'both hosts must render the shared save bar')
  }
  assert.doesNotMatch(prompts, /<textarea[^>]*value=\{draft\.body/, 'the drawer must not re-implement the form')

  // The drawer creates and edits in place.
  assert.ok(prompts.includes('openEditor(null)'), 'the drawer needs a New control')
  assert.ok(prompts.includes('openEditor(item)'), 'each row must be editable in place')
  // Its dismissals cannot be intercepted, so an open draft is mirrored.
  assert.ok(prompts.includes('persistKey:'))
  assert.ok(editor.includes('sessionStorage.setItem(stashKey'))
  assert.ok(editor.includes('stash.revision !== stashRevision'), 'a stale stash must be dropped, not replayed')
})

test('the full library selects straight into an editable form and can widen past one Project', () => {
  const library = source('PromptLibrary.tsx')

  // No Edit mode to enter: selecting a template is editing it.
  assert.doesNotMatch(library, /beginEdit|>Edit</)
  assert.ok(library.includes('all_projects=1'), 'the wide view is the library’s reason to exist')
  assert.ok(library.includes('prompt-scope-filter'))
  // Widening must not leak into the pinning path, which reads the default listing.
  const prompts = source('PromptsTab.tsx')
  assert.doesNotMatch(prompts, /all_projects/)
  // A template is written back to its own Project, not to whichever is focused.
  assert.ok(source('PromptTemplateEditor.tsx').includes('draft.projectId || project?.id'))
})
