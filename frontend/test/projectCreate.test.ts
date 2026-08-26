import assert from 'node:assert/strict'
import test from 'node:test'
import {
  commonestParent, defaultInitScriptSelection, emptyProjectCreateDraft, joinPath, parentPath,
  projectCreateReady, projectCreateRoot, selectedStartingSets, suggestFolderName,
  toggleInitScript, type StartingSetCatalog,
} from '../src/projectCreate.ts'

test('a parent and a folder name join with the separator the parent already uses', () => {
  assert.equal(joinPath('D:\\projects', 'horizon'), 'D:\\projects\\horizon')
  assert.equal(joinPath('D:\\projects\\', 'horizon'), 'D:\\projects\\horizon')
  assert.equal(joinPath('/home/j/code', 'horizon'), '/home/j/code/horizon')
  assert.equal(joinPath('D:', 'horizon'), 'D:\\horizon')
  assert.equal(joinPath('', 'horizon'), 'horizon')
  assert.equal(joinPath('D:\\projects', ''), 'D:\\projects')
})

test('a folder name suggestion strips what a filesystem will not take', () => {
  assert.equal(suggestFolderName('  Horizon Web  '), 'Horizon-Web')
  assert.equal(suggestFolderName('api: v2/next'), 'api-v2-next')
  assert.equal(suggestFolderName('...'), '')
})

test('existing-folder mode registers the typed root untouched', () => {
  const draft = {...emptyProjectCreateDraft(), name:'Horizon', root:' D:\\projects\\horizon '}
  assert.equal(projectCreateRoot(draft), 'D:\\projects\\horizon')
  assert.equal(projectCreateReady(draft), true)
  assert.equal(projectCreateReady({...draft, root:''}), false)
})

test('new-folder mode tracks the name until the folder field is edited', () => {
  const draft = {...emptyProjectCreateDraft(), mode:'new' as const, name:'Horizon Web', parent:'D:\\projects'}
  assert.equal(projectCreateRoot(draft), 'D:\\projects\\Horizon-Web')
  const edited = {...draft, folder:'horizon', folderTouched:true}
  assert.equal(projectCreateRoot(edited), 'D:\\projects\\horizon')
  // A name that slugifies to nothing leaves no root to submit.
  assert.equal(projectCreateReady({...draft, name:'...'}), false)
  assert.equal(projectCreateReady({...draft, parent:''}), false)
})

test('a root path names its parent, keeping a drive letter usable', () => {
  assert.equal(parentPath('D:\\projects\\horizon'), 'D:\\projects')
  assert.equal(parentPath('D:\\horizon'), 'D:\\')
  assert.equal(parentPath('/home/j/code/horizon'), '/home/j/code')
  assert.equal(parentPath('D:\\projects\\horizon\\'), 'D:\\projects')
  assert.equal(parentPath('horizon'), '')
  assert.equal(parentPath(''), '')
})

test('the commonest parent of the registered roots is the settings placeholder', () => {
  assert.equal(commonestParent([
    'D:\\projects\\a', 'D:\\projects\\b', 'd:\\PROJECTS\\c', 'D:\\other\\x',
  ]), 'D:\\projects') // case-insensitive count; first-seen spelling wins
  assert.equal(commonestParent(['/home/j/code/a', '/home/j/code/b']), '/home/j/code')
  assert.equal(commonestParent([]), '')
  assert.equal(commonestParent(['loose']), '')
})

test('only the free starting set is ticked by default', () => {
  // The free set reads what swe-mux already captures; the model-backed set can bill
  // and the autonomy set hands agents real authority, so each of those is a
  // deliberate choice and never part of the name-folder-Enter path.
  const draft = emptyProjectCreateDraft()
  assert.equal(draft.automations, true)
  assert.equal(draft.llm, false)
  assert.equal(draft.autonomy, false)
})

const CATALOG: StartingSetCatalog = {
  recommended: {automations: ['doc_debt', 'code_graph'], values: {}},
  llm: {automations: ['scan_timeline', 'continuous_title'], values: {scan_timeline_auto_enable: true}},
  autonomy: {automations: ['session_control', 'land_queue', 'observation_inbox'],
    values: {spawn_grant: 'granted', land_grant: 'granted'}},
}

test('the ticked starting sets union into one grant request', () => {
  const draft = {...emptyProjectCreateDraft(), llm: true, autonomy: true}
  assert.deepEqual(selectedStartingSets(draft, CATALOG), {
    automations: ['doc_debt', 'code_graph', 'scan_timeline', 'continuous_title',
      'session_control', 'land_queue', 'observation_inbox'],
    values: {scan_timeline_auto_enable: true, spawn_grant: 'granted', land_grant: 'granted'},
  })
  assert.deepEqual(selectedStartingSets({...draft, automations: false, llm: false, autonomy: false}, CATALOG),
    {automations: [], values: {}})
})

test('an id two ticked sets share is requested once', () => {
  const overlapping = {...CATALOG, autonomy: {automations: ['doc_debt'], values: {}}}
  const draft = {...emptyProjectCreateDraft(), autonomy: true}
  assert.deepEqual(selectedStartingSets(draft, overlapping).automations, ['doc_debt', 'code_graph'])
})

test('init scripts start unchecked unless their definition opts in', () => {
  const scripts = [
    {id:'git', label:'Initialize git', command:'git init', default_enabled:true},
    {id:'code', label:'Workspace', command:'code -n .'},
  ]
  assert.deepEqual(defaultInitScriptSelection(scripts), ['git'])
  assert.deepEqual(toggleInitScript(['git'], 'code', true), ['git','code'])
  assert.deepEqual(toggleInitScript(['git','code'], 'git', false), ['code'])
  assert.deepEqual(toggleInitScript(['git'], 'git', true), ['git'])
})
