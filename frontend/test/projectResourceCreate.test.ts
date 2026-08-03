import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { projectResourceCreationParent } from '../src/projectResourceCreate.ts'

test('resource creation targets a folder or a file sibling without accepting a nested name', () => {
  assert.equal(projectResourceCreationParent('src/components', 'directory'), 'src/components')
  assert.equal(projectResourceCreationParent('src/components/App.tsx', 'file'), 'src/components')
  assert.equal(projectResourceCreationParent('README.md', 'file'), '')
  assert.equal(projectResourceCreationParent('', 'directory'), '')
})

test('Files creation is available only through context menus and guarded touch long-press', () => {
  const source = readFileSync(join(import.meta.dirname, '..', 'src', 'ProjectResource.tsx'), 'utf8')
  assert.match(source, />New file…<\/button>/)
  assert.match(source, />New folder…<\/button>/)
  assert.match(source, /event\.pointerType==='touch'/)
  assert.match(source, /suppressTreeClick\.current=true/)
  assert.match(source, /onContextMenu=\{backgroundTreeMenu\}/)
  assert.doesNotMatch(source, /aria-label="(?:Create|New) (?:file|folder)"[^>]*>\+/)
})
