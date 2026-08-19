import assert from 'node:assert/strict'
import {
  BARGE_IN_CONFIRM_FRAMES,
  HOLD_ENTER_PHRASES,
  HOLD_RELEASE_PHRASES,
  PLAYBACK_PROBE_CONFIRM_FRAMES,
  PLAYBACK_PROBE_REJECT_FRAMES,
  PLAYBACK_PROBE_SETTLE_FRAMES,
  PlaybackSpeechProbe,
  buildVoiceMatcher,
  isPlaybackControl,
  matchesBarePhrase,
  nextBargeInFrameCount,
  parseMuxVoice,
  playbackProbeCandidate,
} from '../src/conversation.ts'

// Bare hold phrases match only when the utterance IS the phrase: a sentence
// merely containing "go ahead" must never be eaten by the release check.
assert.equal(matchesBarePhrase('Hold on.',HOLD_ENTER_PHRASES),true)
assert.equal(matchesBarePhrase('  let me think  ',HOLD_ENTER_PHRASES),true)
assert.equal(matchesBarePhrase('Hold on while I check the config',HOLD_ENTER_PHRASES),false)
assert.equal(matchesBarePhrase('Go ahead!',HOLD_RELEASE_PHRASES),true)
assert.equal(matchesBarePhrase('What do you think',HOLD_RELEASE_PHRASES),true)
assert.equal(matchesBarePhrase('go ahead and spawn a session',HOLD_RELEASE_PHRASES),false)

assert.deepEqual(parseMuxVoice('Please run the focused tests. Mux send that.'),{
  command:'send',text:'Please run the focused tests.',
})
assert.deepEqual(parseMuxVoice('Mux, send'),{command:'send',text:''})
assert.deepEqual(parseMuxVoice('Keep this in the composer. Mux append'),{command:'append',text:'Keep this in the composer.'})
assert.deepEqual(parseMuxVoice('Okay mux submit message'),{command:'send',text:''})
assert.deepEqual(parseMuxVoice('Hey mux, cancel that'),{command:'cancel',text:''})
assert.deepEqual(parseMuxVoice('Keep the current implementation. Mucks submit it'),{
  command:'send',text:'Keep the current implementation.',
})
assert.deepEqual(parseMuxVoice('Mux cancel that'),{command:'cancel',text:''})
assert.deepEqual(parseMuxVoice('Mux undo that'),{command:'undo',text:''})
assert.deepEqual(parseMuxVoice('Mux stop speaking'),{command:'mute',text:''})
assert.deepEqual(parseMuxVoice('Mux stop'),{command:'mute',text:''})
assert.deepEqual(parseMuxVoice('Mux read the reply again'),{command:'read',text:''})
assert.deepEqual(parseMuxVoice('Mux summary mode'),{command:'summary',text:''})
assert.deepEqual(parseMuxVoice('Mux use summaries'),{command:'summary',text:''})
assert.deepEqual(parseMuxVoice('Mux read verbatim'),{command:'verbatim',text:''})
assert.deepEqual(parseMuxVoice('Mux list commands'),{command:'help',text:''})
assert.deepEqual(parseMuxVoice('Mux stop listening'),{command:'stop',text:''})
assert.deepEqual(parseMuxVoice('Actually stop this run. Mux interrupt the agent'),{
  command:'interrupt',text:'Actually stop this run.',
})
assert.deepEqual(parseMuxVoice('Mux go to sleep'),{command:'standby',text:''})
assert.deepEqual(parseMuxVoice('Mux resume'),{command:'resume',text:''})
assert.deepEqual(parseMuxVoice('Mux, just listen'),{command:'hold',text:''})
assert.deepEqual(parseMuxVoice('Mux brainstorm'),{command:'hold',text:''})
assert.deepEqual(parseMuxVoice('And that is my last thought. Mux, go ahead'),{
  command:'proceed',text:'And that is my last thought.',
})
assert.deepEqual(parseMuxVoice('Mux voice comms on'),{command:'comms_on',text:''})
assert.deepEqual(parseMuxVoice('Mux exit voice comms'),{command:'comms_off',text:''})
assert.deepEqual(parseMuxVoice('Explain what a mux is'),{command:null,text:'Explain what a mux is'})
assert.deepEqual(parseMuxVoice('Computer send'),{command:null,text:'Computer send'})
assert.deepEqual(parseMuxVoice('My computer sends notifications'),{command:null,text:'My computer sends notifications'})
assert.equal(isPlaybackControl('mute'),true)
assert.equal(isPlaybackControl('stop'),true)
assert.equal(isPlaybackControl('interrupt'),true)
assert.equal(isPlaybackControl('send'),false)
assert.equal(isPlaybackControl(null),false)

// Configurable wake words + phrases: a custom "swe" trigger with variants, and a
// user-renamed submit phrase. Only configured phrases after a wake word fire.
const swe=buildVoiceMatcher(['swe','swee','sway'],[
  {action:'send',phrases:['go','ship it']},
  {action:'resume',phrases:['wake up']},
  {action:'stop',phrases:['stop listening']},
])
assert.deepEqual(swe.parse('Run the tests. Swe go'),{command:'send',text:'Run the tests.'})
assert.deepEqual(swe.parse('Swee ship it'),{command:'send',text:''})
assert.deepEqual(swe.parse('sway wake up'),{command:'resume',text:''})
assert.deepEqual(swe.parse('Swe send'),{command:null,text:'Swe send'})
assert.deepEqual(swe.parse('go ahead and merge'),{command:null,text:'go ahead and merge'})

assert.equal(playbackProbeCandidate(.5,.02,.004),true,'quiet speech must be able to interrupt playback')
assert.equal(playbackProbeCandidate(.49,.08,.004),false)
assert.equal(playbackProbeCandidate(.9,.007,.004),false)

const confirmedProbe=new PlaybackSpeechProbe()
assert.deepEqual(confirmedProbe.step(false,true),{action:'none',collect:false})
assert.deepEqual(confirmedProbe.step(true,true),{action:'duck',collect:false})
for(let index=0;index<PLAYBACK_PROBE_SETTLE_FRAMES;index++){
  assert.deepEqual(confirmedProbe.step(true,true),{action:'none',collect:false})
}
for(let index=1;index<PLAYBACK_PROBE_CONFIRM_FRAMES;index++){
  assert.deepEqual(confirmedProbe.step(true,true),{action:'none',collect:true})
}
assert.deepEqual(confirmedProbe.step(true,true),{action:'confirm',collect:true})
assert.equal(confirmedProbe.probing,false)

const rejectedProbe=new PlaybackSpeechProbe()
assert.deepEqual(rejectedProbe.step(true,true),{action:'duck',collect:false})
for(let index=0;index<PLAYBACK_PROBE_SETTLE_FRAMES;index++)rejectedProbe.step(false,true)
for(let index=1;index<PLAYBACK_PROBE_REJECT_FRAMES;index++){
  assert.deepEqual(rejectedProbe.step(false,true),{action:'none',collect:false})
}
assert.deepEqual(rejectedProbe.step(false,true),{action:'restore',collect:false})
assert.equal(rejectedProbe.probing,false)

const endedProbe=new PlaybackSpeechProbe()
assert.deepEqual(endedProbe.step(true,true),{action:'duck',collect:false})
assert.deepEqual(endedProbe.step(true,false),{action:'restore',collect:false})
let bargeFrames=0
bargeFrames=nextBargeInFrameCount(bargeFrames,.9)
bargeFrames=nextBargeInFrameCount(bargeFrames,.9)
assert.equal(bargeFrames,BARGE_IN_CONFIRM_FRAMES-1)
assert.equal(nextBargeInFrameCount(bargeFrames,.9),BARGE_IN_CONFIRM_FRAMES)
assert.equal(nextBargeInFrameCount(bargeFrames,0),0)

console.log('conversation tests passed')
