import assert from 'node:assert/strict'
import test from 'node:test'
import {
  commonestParent, defaultInitScriptSelection, emptyProjectCreateDraft, joinPath, parentPath,
  projectCreateReady, projectCreateRoot, suggestFolderName, toggleInitScript,
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
