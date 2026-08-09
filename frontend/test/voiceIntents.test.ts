import assert from 'node:assert/strict'
import type { Command } from '../src/commands.ts'
import { extractWakeIntent, normalizeSpokenText, numberedCandidates, resolveVoiceIntent, selectNumberedCandidate } from '../src/voiceIntents.ts'
import { sessionLaunchVoicePhrases } from '../src/voiceLaunch.ts'

const command = (id:string, label:string, phrases:string[], available=true):Command => ({
  id, label, category:'voice', available, run:()=>{}, voice:{phrases},
})
const commands = [
  command('session.focus:a','Focus Alpha',['go to alpha','open alpha']),
  command('session.focus:b','Focus Beta',['go to beta','open beta']),
  command('session.spawn','New Claude in Alpha',['new claude in alpha','new claude in alpha {text}']),
  command('voice.query','Voice query',['{text}']),
]

assert.equal(normalizeSpokenText('Okay, please open Project Two!'),'open project 2')
assert.equal(extractWakeIntent('Mux, go to alpha',['mux']),'go to alpha')
assert.equal(extractWakeIntent('Go to alpha',['mux']),null)
assert.equal(resolveVoiceIntent(commands,'go to alpha').match?.command.id,'session.focus:a')
assert.equal(resolveVoiceIntent(commands,'new claude in alpha fix the tests').match?.text,'fix the tests')
assert.equal(resolveVoiceIntent(commands,'open nowhere').match?.command.id,'voice.query')

const ambiguous = resolveVoiceIntent([
  command('a','First',['open it']), command('b','Second',['open it']),
],'open it')
assert.equal(ambiguous.match,null)
assert.deepEqual(ambiguous.candidates.map(item=>item.command.id),['a','b'])
assert.equal(numberedCandidates(ambiguous.candidates),'1. First 2. Second')
assert.equal(selectNumberedCandidate(ambiguous.candidates,'option two')?.command.id,'b')

const launchCommands = [
  command('spawn:one','New Codex in Alpha',sessionLaunchVoicePhrases({
    backend:'codex',displayName:'Codex',projectName:'Alpha',projectNumber:1,currentProject:true,
  })),
  command('spawn:two','New Codex in Beta',sessionLaunchVoicePhrases({
    backend:'codex',displayName:'Codex',projectName:'Beta',projectNumber:2,currentProject:false,
  })),
]
assert.equal(resolveVoiceIntent(launchCommands,'new codex').match?.command.id,'spawn:one')
assert.equal(resolveVoiceIntent(launchCommands,'new codex fix the tests').match?.command.id,'spawn:one')
assert.equal(resolveVoiceIntent(launchCommands,'new codex in project two').match?.command.id,'spawn:two')
const numberedSeed=resolveVoiceIntent(launchCommands,'new codex in project two fix the tests').match
assert.equal(numberedSeed?.command.id,'spawn:two')
assert.equal(numberedSeed?.text,'fix the tests')
assert.ok(sessionLaunchVoicePhrases({
  backend:'omp',displayName:'oh-my-pi',projectName:'Alpha',projectNumber:1,currentProject:true,
}).includes('new omp'))

console.log('voice intent tests passed')
