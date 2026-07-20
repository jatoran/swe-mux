import assert from 'node:assert/strict'
import test from 'node:test'
import { folderNameFromPath } from '../src/pathNames.ts'

test('folder picker derives an editable project name from Windows and POSIX paths',()=>{
  assert.equal(folderNameFromPath('D:\\projects\\horizon'),'horizon')
  assert.equal(folderNameFromPath('D:\\projects\\pixel-lab\\'),'pixel-lab')
  assert.equal(folderNameFromPath('/work/orca/'),'orca')
})

test('folder picker gives filesystem roots useful names',()=>{
  assert.equal(folderNameFromPath('D:\\'),'D')
  assert.equal(folderNameFromPath('/'),'/')
  assert.equal(folderNameFromPath(''),'')
})
