import assert from 'node:assert/strict'
import test from 'node:test'
import { defaultRailConfig, type RailItem } from '../src/commandRail.ts'
import { resolveRailVoiceEntries } from '../src/railVoice.ts'

const context = (backend='codex') => ({ device: 'desktop' as const, backend })

test('default rail voice exposes only explicit safe session actions', () => {
  const entries=resolveRailVoiceEntries(defaultRailConfig(),context())
  const ids=entries.map(entry=>entry.item.id)
  assert.equal(ids.includes('paste'),true)
  assert.equal(ids.includes('esc'),true)
  assert.equal(ids.includes('enter'),true)
  assert.equal(ids.includes('ctrlC'),true)
  assert.equal(ids.includes('newline'),true)
  assert.deepEqual(entries.find(entry=>entry.item.id==='paste')?.request,{action:'pasteText'})
  // `ctrlU` is a raw key and would be voice-reachable the moment it grew a phrase, so
  // its omission is the enforced half of the rule: destroying an unseen draft is not a
  // thing a spoken caller may do, while `restoreInput` — the recovering half — is voiced.
  for(const excluded of ['attach','kbdToggle','endSession','relaunch','branch','ctrlU','copyInput','copyReply','copyResume']){
    assert.equal(ids.includes(excluded),false,`${excluded} must not be voice-accessible`)
  }
  assert.equal(ids.includes('restoreInput'),true)
})

test('placement controls voice availability and duplicate placements collapse', () => {
  const config=defaultRailConfig()
  config.layouts.desktop.strip=[{id:'one',items:['esc','esc','enter','esc']}]
  assert.deepEqual(resolveRailVoiceEntries(config,context()).map(entry=>entry.item.id),['esc','enter'])
  config.layouts.desktop.strip[0].items=['enter']
  assert.deepEqual(resolveRailVoiceEntries(config,context()).map(entry=>entry.item.id),['enter'])
})

test('configured skills and slash commands derive backend-aware voice actions', () => {
  const config=defaultRailConfig()
  const custom:RailItem[]=[
    {id:'skill:learn',type:'skill',label:'Learn',text:'learn'},
    {id:'slash:review',type:'slash',label:'Review',text:'review',submit:true},
    {id:'text:unsafe',type:'text',label:'Deploy',text:'deploy now',voicePhrases:['deploy']},
    {id:'prompt:unsafe',type:'prompt',label:'Prompt',promptKey:'global:x',voicePhrases:['prompt']},
  ]
  config.items.push(...custom)
  config.layouts.desktop.strip[0].items.push(...custom.map(item=>item.id))

  const codex=resolveRailVoiceEntries(config,context('codex'))
  const learn=codex.find(entry=>entry.item.id==='skill:learn')
  const review=codex.find(entry=>entry.item.id==='slash:review')
  assert.deepEqual(learn?.phrases,['learn','run learn'])
  assert.deepEqual(learn?.request,{action:'insertText',text:'$learn',submit:false})
  assert.equal(review,undefined)
  assert.equal(codex.some(entry=>entry.item.id==='text:unsafe'),false)
  assert.equal(codex.some(entry=>entry.item.id==='prompt:unsafe'),false)

  const claude=resolveRailVoiceEntries(config,context('claude'))
  assert.equal(claude.find(entry=>entry.item.id==='skill:learn')?.request.text,'/learn')
  const shell=resolveRailVoiceEntries(config,context('shell'))
  assert.equal(shell.some(entry=>entry.item.id==='skill:learn'||entry.item.id==='slash:review'),false)
})

test('submitted custom commands need an explicit voice opt-in',()=>{
  const config=defaultRailConfig()
  const item:RailItem={
    id:'slash:review',type:'slash',label:'Review',text:'review',submit:true,
    voicePhrases:['run review'],
  }
  config.items.push(item)
  config.layouts.desktop.strip[0].items.push(item.id)
  const entry=resolveRailVoiceEntries(config,context('claude')).find(candidate=>candidate.item.id===item.id)
  assert.deepEqual(entry?.request,{action:'insertText',text:'/review',submit:true})
})

test('an explicitly voiced custom key uses the normal key path', () => {
  const config=defaultRailConfig()
  const item:RailItem={id:'key:f12',type:'key',label:'F12',bytes:'\x1b[24~',voicePhrases:['press f twelve']}
  config.items.push(item)
  config.layouts.desktop.strip[0].items.push(item.id)
  const entry=resolveRailVoiceEntries(config,context()).find(candidate=>candidate.item.id===item.id)
  assert.deepEqual(entry?.request,{action:'sendKey',text:'\x1b[24~'})
})
