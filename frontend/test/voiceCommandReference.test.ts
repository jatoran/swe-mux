import assert from 'node:assert/strict'
import test from 'node:test'
import type { Command } from '../src/commands.ts'
import { completeVoiceReference, registeredVoiceReference } from '../src/voiceCommandReference.ts'

const command=(id:string,label:string,phrases:string[],available=true,disabledReason?:string):Command=>({
  id,label,category:'voice',available,disabledReason,run:()=>{},voice:{phrases},
})

test('the registered reference groups commands and drops the catch-all', () => {
  const groups=registeredVoiceReference([
    command('voice.query','Catch all',['{text}']),
    command('voice.approval.prepare','Review approval',['approve','approve']),
    command('drawer.show:notes','Open Notes',['open notes']),
    command('project.focus:p1','Focus Alpha',['open project Alpha']),
    command('session.focus:s1','Focus Agent',['open session Agent']),
    command('session.spawn:p1:codex','New Codex in Alpha',['new Codex in Alpha {text}'],false,'Unavailable now'),
  ])
  assert.deepEqual(groups.map(group=>group.id),['status','workspace','projects','sessions','launch'])
  // Duplicate phrases collapse, and the catch-all query is never listed as a command.
  assert.deepEqual(groups[0].commands[0].phrases,['approve'])
  assert.equal(groups.some(group=>group.commands.some(item=>item.id==='voice.query')),false)
  // An unavailable command still appears, carrying the reason it cannot run.
  assert.deepEqual(groups.at(-1)?.commands[0],{
    id:'session.spawn:p1:codex',label:'New Codex in Alpha',phrases:['new Codex in Alpha {text}'],
    available:false,disabledReason:'Unavailable now',
  })
})

test('the complete reference shows every alias, the grammar, and the raw intents', () => {
  const complete=completeVoiceReference([
    command('sidebar.open','Open navigation',['open navigation','open left sidebar']),
    command('drawer.close','Close side panel',['close side panel','close right sidebar']),
    command('session.spawn:p1:codex','New Codex in Alpha',['new Codex','new Codex in Alpha']),
  ], [{action:'send',phrases:['send it']},{action:'stop',phrases:['stop listening']}])
  const displayedPhrases=complete.flatMap(section=>[
    ...section.phrases,
    ...section.commands.flatMap(item=>item.phrases),
  ])
  assert.ok(displayedPhrases.includes('open left sidebar'))
  assert.ok(displayedPhrases.includes('close right sidebar'))
  assert.ok(displayedPhrases.includes('new Codex in Alpha'))
  assert.ok(displayedPhrases.includes('send it'))
  assert.ok(displayedPhrases.includes('stop listening'))
  assert.equal(complete.some(section=>section.id==='grammar:sessions'),true)
})
