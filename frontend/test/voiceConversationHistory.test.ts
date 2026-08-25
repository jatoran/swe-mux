import assert from 'node:assert/strict'
import test from 'node:test'
import {
  appendVoiceConversationEntry, loadVoiceConversationHistory, loadVoiceConversationHistoryOpen,
  saveVoiceConversationHistory, saveVoiceConversationHistoryOpen,
  VOICE_CONVERSATION_HISTORY_KEY, VOICE_CONVERSATION_HISTORY_LIMIT, VOICE_CONVERSATION_HISTORY_OPEN_KEY,
} from '../src/voiceConversationHistory.ts'

/** A fresh in-memory `Storage` per test, so no test reads another's saved history. */
function storageWith(entries:Iterable<[string,string]>=[]){
  const values=new Map<string,string>(entries)
  return {
    values,
    getItem:(key:string)=>values.get(key)||null,
    setItem:(key:string,value:string)=>{values.set(key,value)},
    removeItem:(key:string)=>{values.delete(key)},
  }
}

test('an appended conversation round-trips through storage', () => {
  const storage=storageWith()
  let history=appendVoiceConversationEntry([],'you','  Mux, list sessions.  ',100)
  history=appendVoiceConversationEntry(history,'mux','2 sessions.\nSession 1. Alpha.',101)
  saveVoiceConversationHistory(history,storage)
  assert.deepEqual(loadVoiceConversationHistory(storage),[history[0],history[1]])
})

test('malformed stored entries are dropped rather than surfaced', () => {
  const storage=storageWith([[VOICE_CONVERSATION_HISTORY_KEY,JSON.stringify([
    null,{role:'bad',text:'ignored',at:1},{id:'kept',role:'you',text:'kept',at:2},
  ])]])
  assert.deepEqual(loadVoiceConversationHistory(storage),[{id:'kept',role:'you',text:'kept',at:2}])
})

test('the history is bounded, dropping from the oldest end', () => {
  let history:ReturnType<typeof appendVoiceConversationEntry>=[]
  for(let index=0;index<VOICE_CONVERSATION_HISTORY_LIMIT+5;index++)history=appendVoiceConversationEntry(history,'you',String(index),index)
  assert.equal(history.length,VOICE_CONVERSATION_HISTORY_LIMIT)
  assert.equal(history[0].text,'5')
})

test('saving an empty history removes the key instead of storing an empty list', () => {
  const storage=storageWith()
  saveVoiceConversationHistory(appendVoiceConversationEntry([],'you','something',1),storage)
  saveVoiceConversationHistory([],storage)
  assert.equal(storage.getItem(VOICE_CONVERSATION_HISTORY_KEY),null)
})

test('the history panel defaults to open and remembers being closed', () => {
  const storage=storageWith()
  assert.equal(loadVoiceConversationHistoryOpen(storage),true)
  saveVoiceConversationHistoryOpen(false,storage)
  assert.equal(storage.getItem(VOICE_CONVERSATION_HISTORY_OPEN_KEY),'closed')
  assert.equal(loadVoiceConversationHistoryOpen(storage),false)
  saveVoiceConversationHistoryOpen(true,storage)
  assert.equal(loadVoiceConversationHistoryOpen(storage),true)
})
