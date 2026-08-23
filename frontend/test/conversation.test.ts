import assert from 'node:assert/strict'
import test from 'node:test'
import {
  BARGE_IN_CONFIRM_FRAMES,
  CAPTURE_STALL_MS,
  CaptureFrameWatchdog,
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
  playbackTranscriptVerdict,
} from '../src/conversation.ts'
import { VAD_FRAME_MS } from '../src/audioFrames.ts'
import { SILERO_GATE } from '../src/speechGate.ts'

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
// The settle frames are held back from the *decision* while echo drains, but
// kept as audio: they are the operator's first word, and discarding them clipped
// ~128 ms off the head of every confirmed barge-in.
for(let index=0;index<PLAYBACK_PROBE_SETTLE_FRAMES;index++){
  assert.deepEqual(confirmedProbe.step(true,true),{action:'none',collect:true})
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

// Registered as a subtest rather than asserted at module scope: a top-level
// throw in this file aborts the remaining imports, and the runner then reports
// the shortened run as a pass. Measured while writing these - a deliberate
// failure took the suite from 1,806 tests to 951 and still exited 0.
test('a probe outlives the gap between two words', () => {
  assert.ok(PLAYBACK_PROBE_REJECT_FRAMES*VAD_FRAME_MS>=200,
    'a reject window shorter than an inter-word gap rejects real speech')
  assert.ok(PLAYBACK_PROBE_REJECT_FRAMES*VAD_FRAME_MS<SILERO_GATE.endpointSilenceMs,
    'a probe must not outlive the utterance it is probing')
  // At three frames this rejected real speech: five rejections against three
  // confirmations in one measured session, four inside 3.6 seconds while the
  // operator was audibly talking (peak RMS 0.29).
  const patient=new PlaybackSpeechProbe()
  patient.step(true,true)
  for(let index=0;index<PLAYBACK_PROBE_SETTLE_FRAMES;index++)patient.step(true,true)
  // Four quiet frames - 128 ms, a stop consonant - then speech resumes.
  for(let index=0;index<4;index++){
    assert.deepEqual(patient.step(false,true),{action:'none',collect:false})
  }
  for(let index=1;index<PLAYBACK_PROBE_CONFIRM_FRAMES;index++)patient.step(true,true)
  assert.deepEqual(patient.step(true,true),{action:'confirm',collect:true})
})

const endedProbe=new PlaybackSpeechProbe()
assert.deepEqual(endedProbe.step(true,true),{action:'duck',collect:false})
assert.deepEqual(endedProbe.step(true,false),{action:'restore',collect:false})
let bargeFrames=0
bargeFrames=nextBargeInFrameCount(bargeFrames,.9)
bargeFrames=nextBargeInFrameCount(bargeFrames,.9)
assert.equal(bargeFrames,BARGE_IN_CONFIRM_FRAMES-1)
assert.equal(nextBargeInFrameCount(bargeFrames,.9),BARGE_IN_CONFIRM_FRAMES)
assert.equal(nextBargeInFrameCount(bargeFrames,0),0)

// Capture frame watchdog: a dead capture and a quiet room must stop rendering
// identically. Frames of silence still arrive as blocks, so "no frames at all"
// is the discriminator, and the clock is injected so the state machine is
// testable without an audio graph.
let clock=0
const captureWatchdog=new CaptureFrameWatchdog(()=>clock)
clock=CAPTURE_STALL_MS-1
assert.deepEqual(captureWatchdog.check(),{action:'none',silentMs:CAPTURE_STALL_MS-1})
assert.equal(captureWatchdog.isStalled,false)
clock=CAPTURE_STALL_MS
assert.equal(captureWatchdog.check().action,'stall','the threshold crossing reports exactly one stall')
assert.equal(captureWatchdog.isStalled,true)
assert.equal(captureWatchdog.recoveryAttempts,1)
assert.equal(captureWatchdog.check().action,'retry','later polls retry instead of re-reporting')
assert.equal(captureWatchdog.recoveryAttempts,2)
assert.equal(captureWatchdog.frame(),true,'the first frame after an outage ends the stall')
assert.equal(captureWatchdog.isStalled,false)
assert.equal(captureWatchdog.recoveryAttempts,2,'the outage keeps its attempt count for the recovery report')
assert.equal(captureWatchdog.frame(),false,'healthy frames report nothing')
assert.equal(captureWatchdog.check().action,'none')
clock+=CAPTURE_STALL_MS
assert.equal(captureWatchdog.check().action,'stall','a fresh outage is reported again')
assert.equal(captureWatchdog.recoveryAttempts,1,'and restarts the attempt counter')

console.log('conversation tests passed')

test('the echo policy refuses suspicion, not measurement', () => {
  // A confirmed barge-in is a measurement: capture muted playback and then
  // required clean speech frames against the silence. Refusing it transcribed a
  // full spoken sentence and answered "Playback command ignored".
  const verdict=(over:Record<string,unknown>)=>playbackTranscriptVerdict({
    playbackAtStart:true,bargeInConfirmed:false,playbackOriginAtStart:'system',
    isPlaybackControl:false,wakeIntent:null,wakeIntentIsSafeQuery:false,...over,
  } as Parameters<typeof playbackTranscriptVerdict>[0])

  assert.deepEqual(verdict({bargeInConfirmed:true}),{action:'deliver'},
    'a confirmed barge-in is ordinary speech and reaches the assistant')
  assert.deepEqual(verdict({bargeInConfirmed:true,playbackOriginAtStart:'agent'}),{action:'deliver'},
    'the proof holds whichever audio was interrupted')
  assert.deepEqual(verdict({playbackAtStart:false}),{action:'deliver'},'no overlap, no policy')
  assert.deepEqual(verdict({isPlaybackControl:true}),{action:'deliver'},
    '"stop" and "mute" always get through, confirmed or not')

  // Unconfirmed overlap keeps every refusal it had: this is the guard that stops
  // the assistant being fed its own words.
  assert.deepEqual(verdict({}),{action:'refuse',reason:'system-unaddressed'})
  assert.deepEqual(verdict({wakeIntent:'delete everything'}),
    {action:'refuse',reason:'system-unsafe'},
    'the wake word alone does not license a mutation during app speech')
  assert.deepEqual(verdict({playbackOriginAtStart:'agent'}),{action:'refuse',reason:'agent-echo'})
  assert.deepEqual(verdict({wakeIntent:'fleet status',wakeIntentIsSafeQuery:true}),
    {action:'deliver-query'})
})
