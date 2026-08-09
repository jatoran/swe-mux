import assert from 'node:assert/strict'
import {
  appendVoiceConversationEntry, loadVoiceConversationHistory, saveVoiceConversationHistory,
  VOICE_CONVERSATION_HISTORY_KEY, VOICE_CONVERSATION_HISTORY_LIMIT,
} from '../src/voiceConversationHistory.ts'

const values=new Map<string,string>()
const storage={
  getItem:(key:string)=>values.get(key)||null,
  setItem:(key:string,value:string)=>{values.set(key,value)},
  removeItem:(key:string)=>{values.delete(key)},
}

let history=appendVoiceConversationEntry([],'you','  Mux, list sessions.  ',100)
history=appendVoiceConversationEntry(history,'mux','2 sessions.\nSession 1. Alpha.',101)
saveVoiceConversationHistory(history,storage)
assert.deepEqual(loadVoiceConversationHistory(storage),[
  history[0],history[1],
])

values.set(VOICE_CONVERSATION_HISTORY_KEY,JSON.stringify([
  null,{role:'bad',text:'ignored',at:1},{id:'kept',role:'you',text:'kept',at:2},
]))
assert.deepEqual(loadVoiceConversationHistory(storage),[{id:'kept',role:'you',text:'kept',at:2}])

history=[]
for(let index=0;index<VOICE_CONVERSATION_HISTORY_LIMIT+5;index++)history=appendVoiceConversationEntry(history,'you',String(index),index)
assert.equal(history.length,VOICE_CONVERSATION_HISTORY_LIMIT)
assert.equal(history[0].text,'5')

saveVoiceConversationHistory([],storage)
assert.equal(storage.getItem(VOICE_CONVERSATION_HISTORY_KEY),null)

console.log('voice conversation history tests passed')
