import assert from 'node:assert/strict'
import {
  comparableProjectDefaults,
  normalizeIgnorePatterns,
  parseIgnorePatternDraft,
  sameDraftValue,
} from '../src/settingsDraft.ts'

assert.equal(sameDraftValue({b:2,a:{z:3,y:4}},{a:{y:4,z:3},b:2}),true)
assert.equal(sameDraftValue({executable:'powershell.exe'},{executable:'pwsh.exe'}),false)

assert.deepEqual(comparableProjectDefaults([
  {id:'one',default_profile_id:undefined},
  {id:'two',default_profile_id:'pwsh'},
]),[
  {id:'one',default_backend:null,default_profile_id:null},
  {id:'two',default_backend:null,default_profile_id:'pwsh'},
])

const ignoreDraft='node_modules\n'
assert.deepEqual(parseIgnorePatternDraft(ignoreDraft),['node_modules',''])
assert.equal(parseIgnorePatternDraft(ignoreDraft).join('\n'),ignoreDraft)
assert.deepEqual(normalizeIgnorePatterns([' node_modules ','','  ','*.tmp']),['node_modules','*.tmp'])

console.log('settings draft tests passed')
