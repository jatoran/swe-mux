import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const source=readFileSync(join(import.meta.dirname,'..','src','HistoryBrowser.tsx'),'utf8')
const css=readFileSync(join(import.meta.dirname,'..','src','style.css'),'utf8')

test('history detail actions stay ahead of bounded verbose metadata',()=>{
  const actions=source.indexOf('<div class="transcript-actions">')
  const metadata=source.indexOf('<div class="transcript-metadata">')
  assert.ok(actions>0)
  assert.ok(metadata>actions)
  assert.match(source.slice(actions,metadata),/Resume as new/)
  assert.match(css,/\.transcript-actions \{[^}]*flex:none;[^}]*flex-wrap:wrap/)
  assert.match(css,/\.transcript-metadata \{[^}]*max-height:[^;}]+;[^}]*overflow-y:auto/)
  assert.match(css,/\.transcript-metadata small \{[^}]*overflow-wrap:anywhere/)
})
