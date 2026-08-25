import assert from 'node:assert/strict'
import test from 'node:test'
import {
  clearSpokenListContext, loadSpokenListContext, saveSpokenListContext,
  SPOKEN_LIST_CONTEXT_KEY, SPOKEN_LIST_TTL_MS,
  type SpokenListContext,
} from '../src/spokenListContext.ts'

/** A fresh in-memory `Storage` per test, so no test can inherit another's saved context. */
function storageWith(entries:Iterable<[string,string]>=[]){
  const values=new Map<string,string>(entries)
  return {
    values,
    getItem:(key:string)=>values.get(key)||null,
    setItem:(key:string,value:string)=>{values.set(key,value)},
    removeItem:(key:string)=>{values.delete(key)},
  }
}

const sessions:SpokenListContext={
  kind:'sessions',
  ids:['session-a','session-b'],
  compound:true,
  pageFrom:0,
  shownThrough:2,
  expiresAt:1_000+SPOKEN_LIST_TTL_MS,
  lastSpeech:'Session list.',
}

const projects:SpokenListContext={
  kind:'projects',ids:['project-a','project-b'],pageFrom:0,shownThrough:2,
  expiresAt:2_000,lastSpeech:'Project list.',
}

test('a saved session context loads back whole while it is live', () => {
  const storage=storageWith()
  saveSpokenListContext(sessions,storage)
  assert.deepEqual(loadSpokenListContext(1_001,storage),sessions)
})

test('a saved project context loads back whole while it is live', () => {
  const storage=storageWith()
  saveSpokenListContext(projects,storage)
  assert.deepEqual(loadSpokenListContext(1_500,storage),projects)
})

test('an expired context is dropped from storage rather than returned', () => {
  const storage=storageWith()
  saveSpokenListContext(projects,storage)
  assert.equal(loadSpokenListContext(2_000,storage),null)
  assert.equal(storage.getItem(SPOKEN_LIST_CONTEXT_KEY),null)
})

test('a context whose page bounds disagree with its ids is rejected', () => {
  const storage=storageWith([[SPOKEN_LIST_CONTEXT_KEY,JSON.stringify({...sessions,shownThrough:3})]])
  assert.equal(loadSpokenListContext(1_001,storage),null)
})

test('clearing removes the stored context', () => {
  const storage=storageWith()
  saveSpokenListContext(sessions,storage)
  clearSpokenListContext(storage)
  assert.equal(storage.getItem(SPOKEN_LIST_CONTEXT_KEY),null)
})
