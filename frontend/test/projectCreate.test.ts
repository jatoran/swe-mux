import assert from 'node:assert/strict'
import test from 'node:test'
import {
  commonestParent, defaultInitScriptSelection, emptyProjectCreateDraft, joinPath, parentPath,
  projectCreateEffective, projectCreateOverrides, projectCreateReady, projectCreateRoot,
  seedRecommendedOverrides, selectedStartingSets, setCreateAutomation, suggestFolderName,
  toggleInitScript, type InheritedAutomation, type StartingSetCatalog,
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

test('a new Project starts by inheriting, with nothing of its own written down', () => {
  // The two optional sets still start off: one spends money and the other hands
  // agents authority, so each is a deliberate choice about a repository rather
  // than part of the name-folder-Enter path. Everything else is inherited.
  const draft = emptyProjectCreateDraft()
  assert.deepEqual(draft.automationOverrides, {})
  assert.equal(draft.llm, false)
  assert.equal(draft.autonomy, false)
})

const CATALOG: StartingSetCatalog = {
  recommended: {automations: ['doc_debt', 'code_graph'], values: {}},
  llm: {automations: ['scan_timeline', 'continuous_title'], values: {scan_timeline_auto_enable: true}},
  autonomy: {automations: ['session_control', 'land_queue', 'observation_inbox'],
    values: {spawn_grant: 'granted', land_grant: 'granted'}},
}

const entry = (
  id: string, extra: Partial<InheritedAutomation> = {},
): InheritedAutomation => ({
  id, label: id, kind: 'consumer', requires: [], implemented: true, spends: false, ...extra,
})

const REGISTRY: InheritedAutomation[] = [
  entry('raw_store', {kind: 'substrate'}),
  entry('tier0', {kind: 'substrate', requires: ['raw_store']}),
  entry('doc_debt', {requires: ['tier0'], install_default: true}),
  entry('code_graph', {requires: ['tier0']}),
  entry('session_control', {default_on: true, install_default: true}),
  entry('cross_session_interlocks', {requires: ['doc_debt'], implemented: false}),
  entry('scan_timeline', {kind: 'substrate', requires: ['tier0'], spends: true, globally_allowed: false}),
]

test('the ticked starting sets union into one grant request', () => {
  const draft = {...emptyProjectCreateDraft(), llm: true, autonomy: true}
  assert.deepEqual(selectedStartingSets(draft, CATALOG), {
    automations: ['scan_timeline', 'continuous_title',
      'session_control', 'land_queue', 'observation_inbox'],
    values: {scan_timeline_auto_enable: true, spawn_grant: 'granted', land_grant: 'granted'},
  })
  assert.deepEqual(selectedStartingSets({...draft, llm: false, autonomy: false}, CATALOG),
    {automations: [], values: {}})
})

test('an id two ticked sets share is requested once', () => {
  const overlapping = {...CATALOG, autonomy: {automations: ['scan_timeline'], values: {}}}
  const draft = {...emptyProjectCreateDraft(), llm: true, autonomy: true}
  assert.deepEqual(selectedStartingSets(draft, overlapping).automations,
    ['scan_timeline', 'continuous_title'])
})

test('what a new Project runs is the install default with the draft over it', () => {
  const draft = emptyProjectCreateDraft()
  // Inherited on, nothing ticked, nothing written.
  assert.deepEqual(projectCreateEffective(draft, REGISTRY).map(item => item.id),
    ['doc_debt', 'session_control'])
  // A ceiling-blocked row is never effective, whatever the draft says, and an
  // unimplemented one is not offered at all.
  assert.deepEqual(
    projectCreateEffective(
      {...draft, automationOverrides: {scan_timeline: true, cross_session_interlocks: true}},
      REGISTRY,
    ).map(item => item.id),
    ['doc_debt', 'session_control'],
  )
})

test('only a real disagreement with the install is written into the new Project', () => {
  // Ticking a box back to what it already inherits must write nothing: writing the
  // agreeing value would pin the Project to today's answer, which is the failure the
  // whole inheritance layer exists to remove.
  assert.deepEqual(projectCreateOverrides(
    {...emptyProjectCreateDraft(), automationOverrides: {doc_debt: true, code_graph: false}},
    REGISTRY,
  ), {})
  assert.deepEqual(projectCreateOverrides(
    {...emptyProjectCreateDraft(), automationOverrides: {doc_debt: false, code_graph: true}},
    REGISTRY,
  ), {doc_debt: false, code_graph: true})
  // An id this build does not implement never reaches the file.
  assert.deepEqual(projectCreateOverrides(
    {...emptyProjectCreateDraft(), automationOverrides: {cross_session_interlocks: true}},
    REGISTRY,
  ), {})
})

test('a ticked consumer brings its substrate and an unticked one takes its readers', () => {
  const draft = emptyProjectCreateDraft()
  // On pulls the closure in: a consumer without its substrate resolves to blocked.
  assert.deepEqual(setCreateAutomation(draft, REGISTRY, 'code_graph', true),
    {code_graph: true, tier0: true, raw_store: true})
  // Off pushes down to everything that reads from it.
  const withSubstrate = {...draft, automationOverrides: setCreateAutomation(draft, REGISTRY, 'code_graph', true)}
  assert.deepEqual(setCreateAutomation(withSubstrate, REGISTRY, 'tier0', false),
    {raw_store: true, tier0: false, doc_debt: false, code_graph: false,
      cross_session_interlocks: false, scan_timeline: false})
})

test('the free set is pre-ticked only where the install has no opinion', () => {
  const recommended = ['doc_debt', 'code_graph']
  // `code_graph` is inherited off and unmentioned, so it is seeded with its whole
  // closure; `doc_debt` is already inherited on, so it is left to inherit.
  assert.deepEqual(seedRecommendedOverrides(recommended, REGISTRY, {}),
    {code_graph: true, tier0: true, raw_store: true})
  // An operator who turned it off install-wide is not overruled by a create form.
  assert.deepEqual(seedRecommendedOverrides(recommended, REGISTRY, {code_graph: false}), {})
  // Nor is a substrate they turned off dragged back in by a seeded consumer.
  assert.deepEqual(seedRecommendedOverrides(recommended, REGISTRY, {tier0: false}),
    {code_graph: true, raw_store: true})
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
