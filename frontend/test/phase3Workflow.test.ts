import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import test from 'node:test'
import { renderPromptTemplate } from '../src/promptTemplates.ts'
import { itemsInOrder, reorderForHover, reorderTargetForPoint } from '../src/dragReorder.ts'
import { classifySoundEvent, isQuietTime, normalizeSoundPreferences, satisfyingSounds, type SoundPreferences } from '../src/sessionSounds.ts'

test('prompt variables render as text without adding a submit action',()=>{
  assert.equal(renderPromptTemplate('Review {{target}} then {{ target }}.',{target:'src/app.ts'}),'Review src/app.ts then src/app.ts.')
  assert.equal(renderPromptTemplate('Keep {{missing}}.',{}),'Keep {{missing}}.')
})

test('sound classification admits root events and excludes subagents',()=>{
  assert.deepEqual(classifySoundEvent({type:'turn_ended',session_id:'root',payload:{scope:'root'}}),{event:'complete',reason:'root agent turn completed'})
  assert.equal(classifySoundEvent({type:'turn_ended',session_id:'child',payload:{scope:'subagent'}}),null)
  assert.deepEqual(classifySoundEvent({type:'approval_needed',payload:{scope:'root',kind:'input'}})?.event,'attention')
  assert.equal(classifySoundEvent({type:'state_changed',payload:{state:'idle'}})?.event,'waiting')
  assert.equal(classifySoundEvent({type:'session_crashed',payload:{reason:'exit'}})?.event,'failure')
})

test('quiet hours support ranges that cross midnight',()=>{
  const prefs:SoundPreferences={enabled:true,volume:.5,quietStart:'22:00',quietEnd:'07:00',events:{complete:true,waiting:true,attention:true,failure:true,reset:true},eventSounds:{complete:'two-tone',waiting:'two-tone',attention:'two-tone',failure:'two-tone',reset:'two-tone'}}
  assert.equal(isQuietTime(prefs,new Date(2026,1,1,23,0)),true)
  assert.equal(isQuietTime(prefs,new Date(2026,1,1,12,0)),false)
})

test('sound preferences migrate the legacy global choice and allow per-event overrides',()=>{
  const migrated=normalizeSoundPreferences({soundId:'sonar',events:{waiting:false}})
  assert.deepEqual(migrated.eventSounds,{complete:'sonar',waiting:'sonar',attention:'sonar',failure:'sonar',reset:'sonar'})
  assert.equal(migrated.events.waiting,false)

  const customized=normalizeSoundPreferences({soundId:'sonar',customSound:'data:audio/wav;base64,AA==',eventSounds:{complete:'ding',failure:'bong',reset:'custom'}})
  assert.equal(customized.eventSounds.complete,'ding')
  assert.equal(customized.eventSounds.waiting,'sonar')
  assert.equal(customized.eventSounds.failure,'bong')
  assert.equal(customized.eventSounds.reset,'custom')
  assert.equal(normalizeSoundPreferences({soundId:'blip',eventSounds:{failure:'custom'}}).eventSounds.failure,'blip')
})

test('the bundled sound catalog is curated and defaults can reference stable ids',()=>{
  assert.deepEqual(satisfyingSounds.map(sound=>sound.id),['two-tone','bong','thump','blip','sonar','blop','ding'])
  assert.equal(satisfyingSounds.every(sound=>sound.description.length>0),true)
  assert.equal(satisfyingSounds.every(sound=>existsSync(new URL(`../public/notification-sounds/${sound.id}.mp3`,import.meta.url))),true)
})

test('drag previews reorder around either half of a hovered item without losing entries',()=>{
  assert.deepEqual(reorderForHover(['a','b','c'],'a','b','after'),['b','a','c'])
  assert.deepEqual(reorderForHover(['a','b','c'],'c','b','before'),['a','c','b'])
  assert.deepEqual(reorderForHover(['a','b','c'],'b','b','after'),['a','b','c'])
  const settled=reorderForHover(['b','a','c'],'a','b','after')
  assert.deepEqual(settled,['b','a','c'])
  assert.deepEqual(itemsInOrder([{id:'a'},{id:'b'},{id:'c'}],['c','a','b']).map(item=>item.id),['c','a','b'])
})

test('continuous reorder surfaces resolve gaps, edges, and the dragged item to a valid slot',()=>{
  const items=[{id:'a',start:10,end:30},{id:'b',start:50,end:70},{id:'c',start:90,end:110}]
  assert.deepEqual(reorderTargetForPoint(items,'b',0),{id:'a',side:'before'})
  assert.deepEqual(reorderTargetForPoint(items,'b',45),{id:'c',side:'before'})
  assert.deepEqual(reorderTargetForPoint(items,'b',65),{id:'c',side:'before'})
  assert.deepEqual(reorderTargetForPoint(items,'b',500),{id:'c',side:'after'})
  assert.equal(reorderTargetForPoint([{id:'b',start:50,end:70}],'b',60),null)
})
