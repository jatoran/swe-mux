import assert from 'node:assert/strict'
import test from 'node:test'
import {
  closeDrawerFile,
  closeOtherDrawerFiles,
  DRAWER_FILE_TAB_LIMIT,
  EMPTY_DRAWER_FILES,
  activeDrawerFile,
  drawerFilesFor,
  fileTabLabels,
  isDrawerFileOpen,
  isDrawerFileOwned,
  openDrawerFile,
  parseDrawerFiles,
  pruneDrawerFiles,
  serializeDrawerFiles,
  showDrawerFileIndex,
} from '../src/drawerFiles.ts'

const paths = (map: ReturnType<typeof openDrawerFile>, projectId: string) =>
  drawerFilesFor(map, projectId).open.map(tab => tab.path)

test('open sets are per Project, so switching Projects and back restores the rail', () => {
  let map = openDrawerFile(EMPTY_DRAWER_FILES, 'p1', 'src/app.ts')
  map = openDrawerFile(map, 'p2', 'README.md')
  assert.deepEqual(paths(map, 'p1'), ['src/app.ts'])
  assert.deepEqual(paths(map, 'p2'), ['README.md'])
  assert.equal(activeDrawerFile(map, 'p1'), 'src/app.ts')
  assert.deepEqual(paths(map, 'p3'), [])
  assert.equal(activeDrawerFile(map, 'p3'), null)
})

test('a Project with nothing open always answers with the same empty state', () => {
  // Called during render, so a fresh object every time would churn dependency arrays.
  assert.equal(drawerFilesFor(EMPTY_DRAWER_FILES, 'p1'), drawerFilesFor(EMPTY_DRAWER_FILES, 'p2'))
})

test('opening an already-open file selects it and keeps its place in the rail', () => {
  // An editor that reordered its tabs on every revisit would make the rail unlearnable.
  let map = openDrawerFile(EMPTY_DRAWER_FILES, 'p1', 'a.ts')
  map = openDrawerFile(map, 'p1', 'b.ts')
  map = openDrawerFile(map, 'p1', 'c.ts')
  map = openDrawerFile(map, 'p1', 'a.ts')
  assert.deepEqual(paths(map, 'p1'), ['a.ts', 'b.ts', 'c.ts'])
  assert.equal(activeDrawerFile(map, 'p1'), 'a.ts')
  assert.ok(isDrawerFileOpen(map, 'p1', 'b.ts'))
  assert.ok(!isDrawerFileOpen(map, 'p1', 'd.ts'))
})

test('the cap evicts the least recently selected tab', () => {
  let map = EMPTY_DRAWER_FILES
  for (const name of ['a', 'b', 'c']) map = openDrawerFile(map, 'p1', `${name}.ts`, { limit: 3 })
  // `b` is used again, so `a` is now the oldest and is the one that goes.
  map = openDrawerFile(map, 'p1', 'b.ts', { limit: 3 })
  map = openDrawerFile(map, 'p1', 'd.ts', { limit: 3 })
  assert.deepEqual(paths(map, 'p1'), ['b.ts', 'c.ts', 'd.ts'])
  assert.equal(activeDrawerFile(map, 'p1'), 'd.ts')
})

test('the cap never evicts a file with unsaved edits, even when it is the oldest', () => {
  // The cap is a convenience and the text is not, so the list is allowed to exceed it.
  let map = EMPTY_DRAWER_FILES
  for (const name of ['a', 'b']) map = openDrawerFile(map, 'p1', `${name}.ts`, { limit: 2 })
  map = openDrawerFile(map, 'p1', 'c.ts', { limit: 2, keep: ['a.ts'] })
  assert.deepEqual(paths(map, 'p1'), ['a.ts', 'c.ts'])

  map = openDrawerFile(map, 'p1', 'd.ts', { limit: 2, keep: ['a.ts', 'c.ts'] })
  assert.deepEqual(paths(map, 'p1'), ['a.ts', 'c.ts', 'd.ts'])
})

test('the default cap is the one the rail is drawn for', () => {
  let map = EMPTY_DRAWER_FILES
  for (let index = 0; index < DRAWER_FILE_TAB_LIMIT + 3; index += 1) {
    map = openDrawerFile(map, 'p1', `file-${index}.ts`)
  }
  assert.equal(drawerFilesFor(map, 'p1').open.length, DRAWER_FILE_TAB_LIMIT)
  assert.equal(activeDrawerFile(map, 'p1'), `file-${DRAWER_FILE_TAB_LIMIT + 2}.ts`)
})

test('closing the showing tab keeps spatial continuity: next, then previous, then the index', () => {
  let map = EMPTY_DRAWER_FILES
  for (const name of ['a', 'b', 'c']) map = openDrawerFile(map, 'p1', `${name}.ts`)
  map = openDrawerFile(map, 'p1', 'b.ts')
  map = closeDrawerFile(map, 'p1', 'b.ts')
  assert.deepEqual(paths(map, 'p1'), ['a.ts', 'c.ts'])
  assert.equal(activeDrawerFile(map, 'p1'), 'c.ts')

  map = closeDrawerFile(map, 'p1', 'c.ts')
  assert.equal(activeDrawerFile(map, 'p1'), 'a.ts')

  map = closeDrawerFile(map, 'p1', 'a.ts')
  assert.deepEqual(paths(map, 'p1'), [])
  assert.equal(activeDrawerFile(map, 'p1'), null)
  // An emptied Project keeps no slot at all rather than an empty record.
  assert.deepEqual(map, {})
})

test('closing a tab that is not the one showing leaves the selection alone', () => {
  let map = EMPTY_DRAWER_FILES
  for (const name of ['a', 'b', 'c']) map = openDrawerFile(map, 'p1', `${name}.ts`)
  map = openDrawerFile(map, 'p1', 'a.ts')
  map = closeDrawerFile(map, 'p1', 'c.ts')
  assert.deepEqual(paths(map, 'p1'), ['a.ts', 'b.ts'])
  assert.equal(activeDrawerFile(map, 'p1'), 'a.ts')
  assert.equal(closeDrawerFile(map, 'p1', 'gone.ts'), map)
})

test('close others keeps one tab and shows it', () => {
  let map = EMPTY_DRAWER_FILES
  for (const name of ['a', 'b', 'c']) map = openDrawerFile(map, 'p1', `${name}.ts`)
  map = closeOtherDrawerFiles(map, 'p1', 'a.ts')
  assert.deepEqual(paths(map, 'p1'), ['a.ts'])
  assert.equal(activeDrawerFile(map, 'p1'), 'a.ts')
  assert.equal(closeOtherDrawerFiles(map, 'p1', 'a.ts'), map)
  assert.equal(closeOtherDrawerFiles(map, 'p1', 'gone.ts'), map)
})

test('returning to the index closes nothing', () => {
  let map = openDrawerFile(EMPTY_DRAWER_FILES, 'p1', 'a.ts')
  map = showDrawerFileIndex(map, 'p1')
  assert.equal(activeDrawerFile(map, 'p1'), null)
  assert.deepEqual(paths(map, 'p1'), ['a.ts'])
  assert.equal(showDrawerFileIndex(map, 'p1'), map)
})

test('only the showing tab claims a file from its pane, and only while the drawer is open', () => {
  // The other tabs are rail entries with no editor, so they take nothing away from a pane;
  // a closed drawer holds no editor at all and the pane has to take the file back.
  let map = openDrawerFile(EMPTY_DRAWER_FILES, 'p1', 'a.ts')
  map = openDrawerFile(map, 'p1', 'b.ts')
  assert.ok(isDrawerFileOwned(map, 'p1', 'b.ts', true))
  assert.ok(!isDrawerFileOwned(map, 'p1', 'a.ts', true))
  assert.ok(!isDrawerFileOwned(map, 'p1', 'b.ts', false))
  assert.ok(!isDrawerFileOwned(map, 'p2', 'b.ts', true))
})

test('a deleted Project does not keep a slot in device-local storage forever', () => {
  let map = openDrawerFile(EMPTY_DRAWER_FILES, 'p1', 'a.ts')
  map = openDrawerFile(map, 'p2', 'b.ts')
  const pruned = pruneDrawerFiles(map, ['p1'])
  assert.deepEqual(Object.keys(pruned), ['p1'])
  assert.equal(pruneDrawerFiles(pruned, ['p1']), pruned)
})

test('a round trip through storage preserves the rail and its selection', () => {
  let map = openDrawerFile(EMPTY_DRAWER_FILES, 'p1', 'a.ts')
  map = openDrawerFile(map, 'p1', 'b.ts')
  map = openDrawerFile(map, 'p1', 'a.ts')
  const restored = parseDrawerFiles(serializeDrawerFiles(map))
  assert.deepEqual(paths(restored, 'p1'), ['a.ts', 'b.ts'])
  assert.equal(activeDrawerFile(restored, 'p1'), 'a.ts')
})

test('a bad stored shape degrades to nothing open rather than throwing', () => {
  assert.deepEqual(parseDrawerFiles(null), EMPTY_DRAWER_FILES)
  assert.deepEqual(parseDrawerFiles('not json'), EMPTY_DRAWER_FILES)
  assert.deepEqual(parseDrawerFiles('[]'), EMPTY_DRAWER_FILES)
  assert.deepEqual(parseDrawerFiles('{"p1":7}'), EMPTY_DRAWER_FILES)
  assert.deepEqual(parseDrawerFiles('{"p1":{"open":"a.ts"}}'), EMPTY_DRAWER_FILES)
  // Entries that are not `{path}` objects drop out; the survivors still load.
  const partial = parseDrawerFiles('{"p1":{"open":[{"path":"a.ts"},7,{"path":""},{"path":"a.ts"}],"active":"a.ts"}}')
  assert.deepEqual(paths(partial, 'p1'), ['a.ts'])
})

test('an active file that is not in the open set is dropped rather than honoured', () => {
  // It would otherwise render a file with no chip, and so no way back to the index.
  const map = parseDrawerFiles('{"p1":{"open":[{"path":"a.ts"}],"active":"b.ts"}}')
  assert.equal(activeDrawerFile(map, 'p1'), null)
  assert.deepEqual(paths(map, 'p1'), ['a.ts'])
})

test('a chip is a basename, widened only as far as it takes to be unique', () => {
  const labels = fileTabLabels([
    'README.md',
    'frontend/src/index.ts',
    'packaging/index.ts',
    'a/deep/one/index.ts',
    'b/deep/one/index.ts',
  ])
  assert.equal(labels.get('README.md'), 'README.md')
  assert.equal(labels.get('frontend/src/index.ts'), 'src/index.ts')
  assert.equal(labels.get('packaging/index.ts'), 'packaging/index.ts')
  // Two files agreeing three segments deep widen until they stop agreeing.
  assert.equal(labels.get('a/deep/one/index.ts'), 'a/deep/one/index.ts')
  assert.equal(labels.get('b/deep/one/index.ts'), 'b/deep/one/index.ts')
})

test('one open file needs no disambiguation at all', () => {
  assert.deepEqual([...fileTabLabels(['frontend/src/App.tsx'])], [['frontend/src/App.tsx', 'App.tsx']])
})
