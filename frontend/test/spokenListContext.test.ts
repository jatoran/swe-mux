import assert from 'node:assert/strict'
import {
  clearSpokenListContext, loadSpokenListContext, saveSpokenListContext,
  spokenProjectId, spokenSessionHandle, SPOKEN_LIST_CONTEXT_KEY, SPOKEN_LIST_TTL_MS,
  type SpokenListContext,
} from '../src/spokenListContext.ts'

const values=new Map<string,string>()
const storage={
  getItem:(key:string)=>values.get(key)||null,
  setItem:(key:string,value:string)=>{values.set(key,value)},
  removeItem:(key:string)=>{values.delete(key)},
}

const sessions:SpokenListContext={
  kind:'sessions',
  items:[{id:'session-a',agentRunId:'run-a'},{id:'session-b',agentRunId:null}],
  pageFrom:0,
  shownThrough:2,
  expiresAt:1_000+SPOKEN_LIST_TTL_MS,
  lastSpeech:'Session list.',
}
saveSpokenListContext(sessions,storage)
assert.deepEqual(loadSpokenListContext(1_001,storage),sessions)
assert.deepEqual(spokenSessionHandle(loadSpokenListContext(1_001,storage),2),{id:'session-b',agentRunId:null})
assert.equal(spokenSessionHandle(sessions,3),null)

const projects:SpokenListContext={
  kind:'projects',ids:['project-a','project-b'],pageFrom:0,shownThrough:2,
  expiresAt:2_000,lastSpeech:'Project list.',
}
saveSpokenListContext(projects,storage)
assert.deepEqual(loadSpokenListContext(1_500,storage),projects)
assert.equal(spokenProjectId(loadSpokenListContext(1_500,storage),2),'project-b')
assert.equal(spokenProjectId(projects,3),null)

assert.equal(loadSpokenListContext(2_000,storage),null)
assert.equal(storage.getItem(SPOKEN_LIST_CONTEXT_KEY),null)

values.set(SPOKEN_LIST_CONTEXT_KEY,JSON.stringify({...sessions,shownThrough:3}))
assert.equal(loadSpokenListContext(1_001,storage),null)

saveSpokenListContext(sessions,storage)
clearSpokenListContext(storage)
assert.equal(storage.getItem(SPOKEN_LIST_CONTEXT_KEY),null)

console.log('spoken list context tests passed')
