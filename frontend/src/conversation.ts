import { FrameAssembler, frameRms, joinFrames, StreamingDownsampler, VAD_FRAME_MS, VAD_SAMPLE_RATE } from './audioFrames.ts'
import { SileroVad } from './sileroVad.ts'
import { ENERGY_GATE, SILERO_GATE, SpeechGate } from './speechGate.ts'
import type { GateConfig } from './speechGate'
import { CAPTURE_WORKLET_NAME, captureWorkletUrl } from './voiceCaptureWorklet.ts'
import type { CaptureMarks } from './voiceLatency'

export type MuxVoiceCommand='send'|'append'|'cancel'|'undo'|'mute'|'read'|'summary'|'verbatim'|'help'|'stop'|'interrupt'|'standby'|'resume'|'hold'|'proceed'|'comms_on'|'comms_off'
export type ParsedMuxVoice={command:MuxVoiceCommand|null;text:string}
export type VoiceCommandConfig={action:string;phrases:string[]}
export type VoiceMatcher={parse(text:string):ParsedMuxVoice}

// The client executes this fixed action set; wake words and the phrases mapped to
// each action are configurable (daemon config, surfaced via /api/voice). Keep in
// sync with VOICE_COMMAND_ACTIONS / default_voice_commands in config.py — these
// defaults are only the fallback when the daemon has not supplied a config yet.
const VOICE_ACTIONS=new Set<MuxVoiceCommand>(['send','append','cancel','undo','mute','read','summary','verbatim','interrupt','help','standby','resume','hold','proceed','stop','comms_on','comms_off'])
export const DEFAULT_WAKE_WORDS=['mux','mucks','max']
export const DEFAULT_COMMANDS:VoiceCommandConfig[]=[
  {action:'send',phrases:['send','send it','send that','send message','submit','submit it','submit that','submit message']},
  {action:'append',phrases:['append','append it','append that','append message','insert','insert it','insert that']},
  {action:'cancel',phrases:['cancel','cancel that','clear','clear that']},
  {action:'undo',phrases:['undo','undo that','undo last','undo last phrase','delete last','delete last phrase']},
  {action:'mute',phrases:['mute','stop','stop speaking','stop playback','stop audio']},
  {action:'read',phrases:['read','read reply','read the reply','read reply again','read the reply again','read response','speak reply','speak the reply']},
  {action:'summary',phrases:['summary','summary mode','use summaries']},
  {action:'verbatim',phrases:['verbatim','verbatim mode','read verbatim']},
  {action:'interrupt',phrases:['interrupt','interrupt agent','interrupt the agent']},
  {action:'help',phrases:['help','list commands','what can i say']},
  {action:'standby',phrases:['sleep','go to sleep','stand by','standby','pause listening']},
  {action:'resume',phrases:['wake','wake up','resume','start listening']},
  {action:'hold',phrases:['listen','just listen','brainstorm','hold that thought','let me think']},
  {action:'proceed',phrases:['go ahead','your turn','over to you','proceed']},
  {action:'comms_on',phrases:['voice comms','voice comms on','start voice comms','enter voice comms']},
  {action:'comms_off',phrases:['voice comms off','stop voice comms','exit voice comms']},
  {action:'stop',phrases:['stop listening','turn off','shut down']},
]

const normalizePhrase=(value:string):string=>value.replace(/\s+/g,' ').trim().toLowerCase()

/**
 * Bare exact phrases for the chat-mode brainstorm hold: entering ("hold on")
 * and releasing ("go ahead") without a wake word. Deliberately a fixed, tight
 * list matched only when the utterance IS the phrase (nothing else), and only
 * consulted while the assistant is the microphone's addressee — so a longer
 * sentence that merely contains "go ahead" is never eaten.
 */
export const HOLD_ENTER_PHRASES=['hold on','hold up','let me think','let me think out loud',"i'm thinking",'im thinking','just listen','just listening']
export const HOLD_RELEASE_PHRASES=['go ahead','okay go ahead','ok go ahead','go for it','your turn','over to you','what do you think','done thinking',"that's it",'thats it']

export function matchesBarePhrase(text:string,phrases:string[]):boolean{
  const cleaned=normalizePhrase(text).replace(/[.,!?…]+$/,'').trim()
  return phrases.includes(cleaned)
}
const escapeRegex=(value:string):string=>value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')

// Commands are recognized only as an utterance SUFFIX: a wake word followed by a
// known command phrase at the very end. Everything before it is buffered draft
// text. Phrases are matched longest-first so "read the reply again" wins over
// "read", and a bare wake word or an unmatched tail leaves the text as draft.
export function buildVoiceMatcher(wakeWords:string[],commands:VoiceCommandConfig[]):VoiceMatcher{
  const words=[...new Set(wakeWords.map(normalizePhrase).filter(Boolean))].sort((a,b)=>b.length-a.length)
  const map=new Map<string,MuxVoiceCommand>()
  for(const entry of commands){
    if(!VOICE_ACTIONS.has(entry.action as MuxVoiceCommand))continue
    for(const phrase of entry.phrases||[]){
      const key=normalizePhrase(phrase)
      if(key&&!map.has(key))map.set(key,entry.action as MuxVoiceCommand)
    }
  }
  const phrases=[...map.keys()].sort((a,b)=>b.length-a.length)
  if(!words.length||!phrases.length)return {parse:(text:string)=>({command:null,text:text.replace(/\s+/g,' ').trim()})}
  const wakeAlt=words.map(escapeRegex).join('|')
  const phraseAlt=phrases.map(key=>escapeRegex(key).replace(/\s+/g,'\\s+')).join('|')
  const pattern=new RegExp(`\\b(?:hey\\s+|okay\\s+|ok\\s+)?(?:${wakeAlt})\\s*[,;:\\-]?\\s*(${phraseAlt})\\s*[.!?]*$`,'i')
  return {
    parse(text:string):ParsedMuxVoice{
      const cleaned=text.replace(/\s+/g,' ').trim()
      const match=pattern.exec(cleaned)
      if(!match)return {command:null,text:cleaned}
      return {command:map.get(normalizePhrase(match[1]))||null,text:cleaned.slice(0,match.index).trim()}
    },
  }
}

const defaultMatcher=buildVoiceMatcher(DEFAULT_WAKE_WORDS,DEFAULT_COMMANDS)

export function parseMuxVoice(text:string):ParsedMuxVoice{
  return defaultMatcher.parse(text)
}

/** Commands that must retain their normal meaning when their speech began over playback. */
export function isPlaybackControl(command:MuxVoiceCommand|null):boolean{
  return command==='mute'||command==='stop'||command==='interrupt'
}

export type ConversationCapability={available:boolean;secureContext:boolean;mediaDevices:boolean;audioContext:boolean;reason:string}

export function conversationCapability():ConversationCapability{
  const secureContext=window.isSecureContext
  const mediaDevices=!!navigator.mediaDevices?.getUserMedia
  const audioContext=typeof AudioContext!=='undefined'
  return {available:secureContext&&mediaDevices&&audioContext,secureContext,mediaDevices,audioContext,
    reason:!secureContext?'HTTPS or localhost is required.':!mediaDevices?'Microphone capture is unavailable in this browser.':!audioContext?'Web Audio is unavailable in this browser.':'Persistent swe-mux microphone capture is available.'}
}

export function encodeWav(samples:Float32Array,sampleRate=16_000):Blob{
  const buffer=new ArrayBuffer(44+samples.length*2)
  const view=new DataView(buffer)
  const ascii=(offset:number,value:string)=>{for(let index=0;index<value.length;index++)view.setUint8(offset+index,value.charCodeAt(index))}
  ascii(0,'RIFF');view.setUint32(4,36+samples.length*2,true);ascii(8,'WAVE');ascii(12,'fmt ')
  view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true)
  view.setUint32(24,sampleRate,true);view.setUint32(28,sampleRate*2,true);view.setUint16(32,2,true);view.setUint16(34,16,true)
  ascii(36,'data');view.setUint32(40,samples.length*2,true)
  for(let index=0;index<samples.length;index++){
    const sample=Math.max(-1,Math.min(1,samples[index]))
    view.setInt16(44+index*2,sample<0?sample*0x8000:sample*0x7fff,true)
  }
  return new Blob([buffer],{type:'audio/wav'})
}

export type CaptureDetector='silero'|'energy'

export const PLAYBACK_PROBE_SETTLE_FRAMES=3
export const PLAYBACK_PROBE_CONFIRM_FRAMES=3
export const PLAYBACK_PROBE_REJECT_FRAMES=3

export type PlaybackProbeAction='none'|'duck'|'restore'|'confirm'
export type PlaybackProbeStep={action:PlaybackProbeAction;collect:boolean}

/**
 * Confirm speech against a quiet microphone instead of demanding that it beat the speaker.
 *
 * The first credible frame ducks app audio. Three frames are then ignored while
 * speaker echo drains, after which three consecutive speech frames confirm a
 * barge-in. Echo that disappears under the duck restores playback.
 */
export class PlaybackSpeechProbe{
  private active=false
  private settleFrames=0
  private confirmFrames=0
  private rejectFrames=0

  get probing():boolean{return this.active}

  step(candidate:boolean,playing:boolean):PlaybackProbeStep{
    if(!this.active){
      if(!playing||!candidate)return{action:'none',collect:false}
      this.active=true
      this.settleFrames=PLAYBACK_PROBE_SETTLE_FRAMES
      this.confirmFrames=0
      this.rejectFrames=0
      return{action:'duck',collect:false}
    }
    if(!playing){this.clear();return{action:'restore',collect:false}}
    if(this.settleFrames>0){this.settleFrames--;return{action:'none',collect:false}}
    if(candidate){
      this.confirmFrames++
      this.rejectFrames=0
      if(this.confirmFrames>=PLAYBACK_PROBE_CONFIRM_FRAMES){this.clear();return{action:'confirm',collect:true}}
      return{action:'none',collect:true}
    }
    this.confirmFrames=0
    this.rejectFrames++
    if(this.rejectFrames>=PLAYBACK_PROBE_REJECT_FRAMES){this.clear();return{action:'restore',collect:false}}
    return{action:'none',collect:false}
  }

  reset():boolean{
    const wasActive=this.active
    this.clear()
    return wasActive
  }

  private clear():void{
    this.active=false
    this.settleFrames=0
    this.confirmFrames=0
    this.rejectFrames=0
  }
}

/** A lower candidate floor is safe because the probe verifies it after ducking playback. */
export function playbackProbeCandidate(probability:number,rms:number,noiseFloor:number):boolean{
  return probability>=.5&&rms>=Math.max(.008,noiseFloor*2.2)
}

/** Three accepted 32 ms frames distinguish real barge-in from a speaker transient. */
export const BARGE_IN_CONFIRM_FRAMES=3

export function nextBargeInFrameCount(current:number,probability:number):number{
  return probability>0?Math.min(BARGE_IN_CONFIRM_FRAMES,current+1):0
}

export type CaptureHandlers={
  onSpeechStart():void
  /** Confirmed user speech over playback. The utterance continues after playback stops. */
  onBargeIn?():void
  /** Temporarily silence app audio while capture distinguishes speech from speaker echo. */
  onPlaybackProbe?(active:boolean):void
  /** Durable diagnostic input for a completed probe. */
  onPlaybackProbeResult?(result:PlaybackProbeResult):void
  /** The endpoint fired. Raised before any text exists, so the UI can acknowledge instantly. */
  onSpeechEnd():void
  /**
   * Enough trailing silence to be worth decoding, while capture keeps listening.
   * The result is usable only while `commitSpeculative` still accepts this id.
   */
  onSpeculative(audio:Blob,marks:CaptureMarks):void
  /** Speech resumed: anything decoded for this id is a prefix and must be dropped. */
  onSpeculativeAbort(utteranceId:string):void
  onUtterance(audio:Blob,marks:CaptureMarks):void
  onError(message:string):void
  /** Reports how capture settled, once the detector is known. */
  onDetector(detector:CaptureDetector,diagnostic:string):void
  /**
   * No audio frames have arrived for CAPTURE_STALL_MS while capture should be
   * running: the microphone is dead, whatever the UI's detector-derived phase
   * says. Raised once per outage; recovery is being attempted alongside.
   */
  onCaptureStall?(diagnostic:CaptureStallDiagnostic):void
  /** Frames are flowing again after a reported stall. */
  onCaptureRecovered?(diagnostic:CaptureStallDiagnostic):void
  playbackActive():boolean
  playbackOrigin?():'agent'|'system'|null
  /**
   * Extra trailing-silence patience in ms, consulted per frame; the silence
   * endpoint fires at max(detector tail, this). Chat mode returns its patience
   * here so thinking out loud is not endpointed at every pause, while the
   * speculative decode still fires at its own threshold and lets a wake-worded
   * command short-circuit the longer tail. Return 0 for the normal tail.
   */
  endpointPatienceMs?():number
}

export type PlaybackProbeResult={
  outcome:'confirmed'|'rejected'
  detector:CaptureDetector
  origin:'agent'|'system'|null
  peakProbability:number
  peakRms:number
}

const newUtteranceId=():string=>globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(36).slice(2)}`

/**
 * No audio frames for this long while capture is supposed to be running means the
 * capture path is dead, not the room quiet: the audio graph pulls continuously, so
 * silence still delivers blocks of zeros at a steady ~21 Hz. Only a suspended
 * AudioContext, a released track, or a killed graph stops the flow entirely.
 */
export const CAPTURE_STALL_MS=5_000
export const CAPTURE_WATCHDOG_POLL_MS=2_000

export type CaptureStallDiagnostic={
  event:'stalled'|'recovered'
  silentMs:number
  contextState:string
  trackState:string
  trackMuted:boolean
  detector:CaptureDetector
  recoveryAttempts:number
}

export type CaptureWatchdogAction='none'|'stall'|'retry'

/**
 * Frame-liveness accounting for the persistent microphone, symptom-level by design.
 *
 * The observed failure (2026-08-20): the phone stopped POSTing audio at 21:53:30
 * while the UI said "listening" for minutes — a dead capture and a quiet room
 * rendered identically because the phase derived only from the speech detector.
 * This class watches the one signal that separates them: whether raw audio blocks
 * are arriving at all. It does not guess *why* they stopped (AudioContext
 * suspension is a hypothesis, not a finding); the caller reports the stall and
 * attempts recovery whatever the cause was.
 *
 * Pure and clock-injected so the stall/recover state machine is unit-testable
 * without an audio graph.
 */
export class CaptureFrameWatchdog{
  // A plain assigned field, not a constructor parameter property: the frontend
  // unit tests run under node's type stripping, which refuses parameter properties.
  private readonly now:()=>number
  private lastFrameAt:number
  private stalled=false
  private attempts=0

  constructor(now:()=>number=()=>performance.now()){this.now=now;this.lastFrameAt=this.now()}

  get isStalled():boolean{return this.stalled}
  get recoveryAttempts():number{return this.attempts}

  /**
   * A raw audio block arrived. Returns true when it ends a reported stall.
   * `recoveryAttempts` keeps the finished outage's count until the next stall
   * begins, so a recovery report can say how many retries the outage took.
   */
  frame():boolean{
    this.lastFrameAt=this.now()
    if(!this.stalled)return false
    this.stalled=false
    return true
  }

  /** Periodic poll: 'stall' exactly once per outage, 'retry' on each later poll. */
  check():{action:CaptureWatchdogAction;silentMs:number}{
    const silentMs=this.now()-this.lastFrameAt
    if(silentMs<CAPTURE_STALL_MS)return{action:'none',silentMs}
    if(this.stalled){this.attempts++;return{action:'retry',silentMs}}
    this.stalled=true
    this.attempts=1
    return{action:'stall',silentMs}
  }
}

/** Audio kept before speech starts, so the leading phoneme is not clipped. */
const PRE_ROLL_MS=320
const PRE_ROLL_FRAMES=Math.ceil(PRE_ROLL_MS/VAD_FRAME_MS)

/**
 * The persistent microphone: one stream, armed across silence, cut into utterances.
 *
 * Detection is Silero over ONNX when it loads and an RMS-over-noise-floor detector
 * when it does not. The two are not interchangeable and the gate config says so:
 * Silero endpoints after 352 ms and speculates at 160 ms, while the energy detector
 * keeps the 900 ms tail it needs to avoid cutting words in half on breath noise, and
 * never speculates. A Silero load failure therefore degrades how dictation feels
 * rather than disabling it.
 *
 * Audio reaches the detector as 512-sample 16 kHz frames whether it came from the
 * AudioWorklet or the ScriptProcessorNode fallback, so the endpoint rules are
 * identical on both paths and there is one resampler to be wrong about.
 */
export class PersistentVoiceCapture{
  private handlers:CaptureHandlers
  private context:AudioContext|null=null
  private stream:MediaStream|null=null
  private source:MediaStreamAudioSourceNode|null=null
  private worklet:AudioWorkletNode|null=null
  private processor:ScriptProcessorNode|null=null
  private sink:GainNode|null=null
  private resampler:StreamingDownsampler|null=null
  private frames=new FrameAssembler()
  private gateConfig:GateConfig=ENERGY_GATE
  // Evaluated per frame, after the constructor has assigned `handlers`; a field
  // initializer cannot read them eagerly, and laziness is what lets the owner
  // change the patience between renders without touching the microphone.
  private readonly patience=()=>this.handlers.endpointPatienceMs?.()||0
  private gate=new SpeechGate(ENERGY_GATE,this.patience)
  private silero:SileroVad|null=null
  private detector:CaptureDetector='energy'
  private preRoll:Float32Array[]=[]
  private utterance:Float32Array[]=[]
  private noiseFloor=0.004
  private utteranceId=''
  private utterancePlayback=false
  private utterancePlaybackOrigin:'agent'|'system'|null=null
  private bargeInFrames:Float32Array[]=[]
  private bargeInConfirmed=false
  private playbackProbe=new PlaybackSpeechProbe()
  private playbackProbeFrames:Float32Array[]=[]
  private playbackProbeOrigin:'agent'|'system'|null=null
  private confirmedPlaybackOrigin:'agent'|'system'|null=null
  private playbackProbePeakProbability=0
  private playbackProbePeakRms=0
  private pending:Promise<void>=Promise.resolve()
  private stopped=false
  /** Push-to-talk suspends endpointing entirely; the key release is the endpoint. */
  private pushToTalk=false
  private watchdog=new CaptureFrameWatchdog()
  private watchdogTimer:ReturnType<typeof setInterval>|null=null

  constructor(handlers:CaptureHandlers){this.handlers=handlers}

  async start():Promise<void>{
    if(this.context)return
    const capability=conversationCapability()
    if(!capability.available)throw new Error(capability.reason)
    this.stopped=false
    this.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false})
    this.context=new AudioContext({latencyHint:'interactive'})
    await this.context.resume()
    // Mobile browsers suspend an AudioContext on memory pressure or audio-focus
    // loss and never resume it for you; the one resume() above was the only one
    // this class ever issued, which is how a dead capture kept reporting
    // "listening". Re-resume on every non-running transition, and let the frame
    // watchdog below catch whatever a resume() cannot fix.
    this.context.onstatechange=()=>{
      const context=this.context
      if(!context||this.stopped)return
      if(context.state!=='running')void context.resume().catch(()=>{/* the watchdog reports it */})
    }
    this.resampler=new StreamingDownsampler(this.context.sampleRate)
    this.source=this.context.createMediaStreamSource(this.stream)
    await this.attachSource()
    this.watchdog=new CaptureFrameWatchdog()
    this.watchdogTimer=setInterval(()=>this.checkCapture(),CAPTURE_WATCHDOG_POLL_MS)
    // Started after capture is live rather than before it: the model is 2.3 MB over
    // whatever link a phone is on, and blocking the microphone on that download would
    // make Talk look broken for seconds. The energy detector is already running until
    // it lands, so no speech is lost while it downloads.
    void this.loadSilero()
  }

  stop():void{
    this.stopped=true
    if(this.watchdogTimer!==null){clearInterval(this.watchdogTimer);this.watchdogTimer=null}
    if(this.playbackProbe.reset())this.handlers.onPlaybackProbe?.(false)
    if(this.context)this.context.onstatechange=null
    if(this.processor)this.processor.onaudioprocess=null
    if(this.worklet)this.worklet.port.onmessage=null
    try{this.source?.disconnect();this.worklet?.disconnect();this.processor?.disconnect();this.sink?.disconnect()}catch{/* already disconnected */}
    for(const track of this.stream?.getTracks()||[])track.stop()
    void this.context?.close()
    this.silero?.dispose();this.silero=null
    this.context=null;this.stream=null;this.source=null;this.worklet=null;this.processor=null;this.sink=null;this.resampler=null
    this.detector='energy';this.gateConfig=ENERGY_GATE
    this.resetUtterance();this.preRoll=[];this.frames.reset();this.pushToTalk=false
  }

  /** Periodic liveness check: no frames for CAPTURE_STALL_MS → report + try to recover. */
  private checkCapture():void{
    if(this.stopped||!this.context)return
    const verdict=this.watchdog.check()
    if(verdict.action==='none')return
    // resume() is idempotent and the least invasive recovery; a track the browser
    // released ('ended') cannot be revived here, and the diagnostic says so.
    void this.context.resume().catch(()=>{/* reported through the diagnostic */})
    if(verdict.action==='stall')this.handlers.onCaptureStall?.(this.captureDiagnostic('stalled',verdict.silentMs))
  }

  private captureDiagnostic(event:'stalled'|'recovered',silentMs:number):CaptureStallDiagnostic{
    const track=this.stream?.getAudioTracks()[0]
    return{
      event,
      silentMs:Math.round(silentMs),
      contextState:this.context?.state||'missing',
      trackState:track?.readyState||'missing',
      trackMuted:!!track?.muted,
      detector:this.detector,
      recoveryAttempts:this.watchdog.recoveryAttempts,
    }
  }

  /**
   * Claim a speculative decode's result and end the utterance on it.
   *
   * Returns false when speech resumed or the utterance already ended by itself, in
   * which case the caller must discard the text: acting on it then would submit a
   * prefix of a sentence still being spoken.
   */
  commitSpeculative(utteranceId:string):boolean{
    if(this.stopped||!this.gate.speaking||!this.gate.speculating)return false
    if(!utteranceId||utteranceId!==this.utteranceId)return false
    this.handlers.onSpeechEnd()
    this.resetUtterance()
    return true
  }

  /** Begin a push-to-talk utterance: capture everything, endpoint on release only. */
  beginPushToTalk():void{
    if(!this.context||this.stopped||this.pushToTalk)return
    this.pushToTalk=true
    const playback=this.handlers.playbackActive()
    if(!this.gate.speaking){
      this.utteranceId=newUtteranceId()
      this.utterancePlayback=playback
      this.utterancePlaybackOrigin=this.handlers.playbackOrigin?.()||null
      this.utterance=this.utterancePlayback?[]:this.preRoll.splice(0)
      this.handlers.onSpeechStart()
    }
    if(playback){
      this.utterance=[]
      this.utterancePlayback=false
      this.bargeInConfirmed=true
      this.handlers.onBargeIn?.()
    }
  }

  /** End a push-to-talk utterance. The key release *is* the endpoint: no tail at all. */
  endPushToTalk():void{
    if(!this.pushToTalk)return
    this.pushToTalk=false
    if(!this.utterance.length){this.resetUtterance();return}
    this.handlers.onSpeechEnd()
    this.finish(0)
  }

  private async attachSource():Promise<void>{
    const context=this.context
    const source=this.source
    if(!context||!source)return
    // Both paths terminate in a muted gain node feeding the destination. The graph is
    // rendered by pulling backwards from the destination, so a capture node with
    // nothing downstream is not guaranteed to run at all.
    this.sink=context.createGain();this.sink.gain.value=0
    this.sink.connect(context.destination)
    if(context.audioWorklet){
      try{
        await context.audioWorklet.addModule(captureWorkletUrl())
        if(this.stopped)return
        this.worklet=new AudioWorkletNode(context,CAPTURE_WORKLET_NAME,{
          numberOfInputs:1,numberOfOutputs:1,outputChannelCount:[1],
          channelCount:1,channelCountMode:'explicit',
        })
        this.worklet.port.onmessage=event=>this.ingest(event.data as Float32Array)
        source.connect(this.worklet);this.worklet.connect(this.sink)
        return
      }catch{/* fall through to the ScriptProcessorNode path */}
    }
    // Kept for browsers without AudioWorklet: it runs its callback on the main
    // thread, so every endpoint decision is jittered by whatever the UI is doing.
    this.processor=context.createScriptProcessor(2048,1,1)
    this.processor.onaudioprocess=event=>this.ingest(event.inputBuffer.getChannelData(0))
    source.connect(this.processor);this.processor.connect(this.sink)
  }

  private async loadSilero():Promise<void>{
    const silero=new SileroVad()
    try{
      await silero.load()
      if(this.stopped){silero.dispose();return}
      this.silero=silero
      this.detector='silero'
      this.gateConfig=SILERO_GATE
      // Swapped only between utterances: changing the endpoint rule mid-utterance
      // would mix 900 ms and 352 ms accounting inside one gate.
      if(!this.gate.speaking)this.gate=new SpeechGate(SILERO_GATE,this.patience)
      this.handlers.onDetector('silero','Silero voice activity detection; utterances end after a short pause.')
    }catch(cause){
      silero.dispose()
      const reason=cause instanceof Error?cause.message:String(cause)
      this.handlers.onDetector('energy',`Silero VAD unavailable (${reason}); using the energy detector, which needs a longer pause.`)
    }
  }

  /** Resample and frame one block of microphone audio, then queue it for detection. */
  private ingest(block:Float32Array):void{
    if(this.stopped)return
    // Liveness first: a block arriving at all is the fact the watchdog needs,
    // whatever the detector later makes of its contents.
    if(this.watchdog.frame())this.handlers.onCaptureRecovered?.(this.captureDiagnostic('recovered',0))
    const resampler=this.resampler
    if(!resampler)return
    const frames=this.frames.push(resampler.push(block))
    if(!frames.length)return
    // Serialized: Silero carries recurrent state between frames, so two overlapping
    // inferences would interleave that state and corrupt both probabilities.
    this.pending=this.pending.then(()=>this.consume(frames)).catch(cause=>{
      if(!this.stopped)this.handlers.onError(cause instanceof Error?cause.message:String(cause))
    })
  }

  private async consume(frames:Float32Array[]):Promise<void>{
    for(const frame of frames){
      if(this.stopped)return
      this.advance(frame,await this.probability(frame))
    }
  }

  private async probability(frame:Float32Array):Promise<number>{
    const silero=this.silero
    const rms=frameRms(frame)
    const playing=this.handlers.playbackActive()
    let probability:number
    if(this.detector==='silero'&&silero?.ready){
      probability=await silero.probability(frame)
      return this.applyPlaybackProbe(frame,probability,rms,playing)
    }
    // Energy fallback, reduced to the same 0/1 signal the gate consumes. Its noise
    // floor is learned only while playback is absent. Teaching speaker output to
    // the room-noise model made a real voice mathematically unable to clear it.
    if(!this.gate.speaking&&!playing&&!this.playbackProbe.probing)this.noiseFloor=this.noiseFloor*.97+Math.min(rms,.04)*.03
    const threshold=Math.max(.012,this.noiseFloor*3.2)
    probability=rms>threshold?1:0
    return this.applyPlaybackProbe(frame,probability,rms,playing)
  }

  private applyPlaybackProbe(frame:Float32Array,probability:number,rms:number,playing:boolean):number{
    if(!playing&&!this.playbackProbe.probing)return probability
    const candidate=playbackProbeCandidate(probability,rms,this.noiseFloor)
    if(!this.playbackProbe.probing&&candidate){
      this.playbackProbeOrigin=this.handlers.playbackOrigin?.()||null
      this.playbackProbePeakProbability=probability
      this.playbackProbePeakRms=rms
    }else if(this.playbackProbe.probing){
      this.playbackProbePeakProbability=Math.max(this.playbackProbePeakProbability,probability)
      this.playbackProbePeakRms=Math.max(this.playbackProbePeakRms,rms)
    }
    const step=this.playbackProbe.step(candidate,playing)
    if(step.action==='duck'){
      this.preRoll=[]
      this.playbackProbeFrames=[]
      this.handlers.onPlaybackProbe?.(true)
      return 0
    }
    if(step.collect)this.playbackProbeFrames.push(frame)
    if(step.action==='confirm'){
      const origin=this.playbackProbeOrigin
      // `advance` adds the current frame itself. Keep the preceding confirmation
      // frames as clean pre-roll, not the playback audio that preceded the duck.
      this.preRoll=this.playbackProbeFrames.slice(0,-1)
      this.confirmedPlaybackOrigin=origin
      this.handlers.onBargeIn?.()
      this.handlers.onPlaybackProbeResult?.(this.playbackProbeResult('confirmed',origin))
      this.clearPlaybackProbe()
      return probability
    }
    if(step.action==='restore'){
      const origin=this.playbackProbeOrigin
      this.handlers.onPlaybackProbe?.(false)
      this.handlers.onPlaybackProbeResult?.(this.playbackProbeResult('rejected',origin))
      this.preRoll=[]
      this.clearPlaybackProbe()
    }
    return 0
  }

  private playbackProbeResult(outcome:'confirmed'|'rejected',origin:'agent'|'system'|null):PlaybackProbeResult{
    return{
      outcome,
      detector:this.detector,
      origin,
      peakProbability:this.playbackProbePeakProbability,
      peakRms:this.playbackProbePeakRms,
    }
  }

  private clearPlaybackProbe():void{
    this.playbackProbeFrames=[]
    this.playbackProbeOrigin=null
    this.playbackProbePeakProbability=0
    this.playbackProbePeakRms=0
  }

  private advance(frame:Float32Array,probability:number):void{
    // Push-to-talk owns its own boundaries: the gate is not allowed to end the
    // utterance while the key is held, because having no endpointing is the point.
    if(this.pushToTalk){this.utterance.push(frame);return}
    const speakingBefore=this.gate.speaking
    if(!speakingBefore){
      this.preRoll.push(frame)
      while(this.preRoll.length>PRE_ROLL_FRAMES)this.preRoll.shift()
    }
    const events=this.gate.push(probability)
    // Appended before the events are handled, so an endpoint on this frame reads a
    // complete utterance and a reset afterwards cannot be undone by a late push.
    if(speakingBefore)this.utterance.push(frame)
    if(this.utterancePlayback&&!this.bargeInConfirmed&&this.gate.speaking){
      if(probability>0){
        this.bargeInFrames.push(frame)
        while(this.bargeInFrames.length>BARGE_IN_CONFIRM_FRAMES)this.bargeInFrames.shift()
      }else this.bargeInFrames=[]
      if(this.bargeInFrames.length>=BARGE_IN_CONFIRM_FRAMES){
        this.bargeInConfirmed=true
        // Drop playback-contaminated pre-roll while retaining all confirmation frames.
        this.utterance=[...this.bargeInFrames]
        this.utterancePlayback=false
        this.handlers.onBargeIn?.()
      }
    }
    for(const event of events){
      if(event.type==='speech-start'){
        this.utteranceId=newUtteranceId()
        const interruptedOrigin=this.confirmedPlaybackOrigin
        this.confirmedPlaybackOrigin=null
        this.utterancePlayback=!!interruptedOrigin||this.handlers.playbackActive()
        this.utterancePlaybackOrigin=interruptedOrigin||this.handlers.playbackOrigin?.()||null
        if(interruptedOrigin)this.bargeInConfirmed=true
        // The pre-roll already holds this frame, so it is not appended again.
        this.utterance=this.preRoll.splice(0)
        if(this.utterancePlayback&&probability>0)this.bargeInFrames=[frame]
        this.handlers.onSpeechStart()
      }else if(event.type==='speculate'){
        this.emit(this.handlers.onSpeculative,this.gate.silenceMs,true)
      }else if(event.type==='resume'){
        this.handlers.onSpeculativeAbort(this.utteranceId)
      }else if(event.type==='endpoint'){
        this.handlers.onSpeechEnd()
        this.finish(event.reason==='silence'?this.gate.silenceMs:0)
      }
    }
  }

  private finish(silenceMs:number):void{
    this.emit(this.handlers.onUtterance,silenceMs,false)
    this.resetUtterance()
  }

  /** Encode what has been captured so far and hand it to one of the two sinks. */
  private emit(sink:(audio:Blob,marks:CaptureMarks)=>void,silenceMs:number,speculative:boolean):void{
    const chunks=this.utterance
    if(!chunks.length)return
    const finishedAt=performance.now()
    try{
      const audio=encodeWav(joinFrames(chunks),VAD_SAMPLE_RATE)
      sink(audio,{
        utteranceId:this.utteranceId,
        // Speech physically stopped one trailing-silence window ago, not now. Dating
        // the utterance from here would hide the endpoint wait — the single largest
        // stage — inside a stage nobody is looking at.
        speechEndAt:finishedAt-silenceMs,
        finishedAt,
        encodedAt:performance.now(),
        audioMs:chunks.length*VAD_FRAME_MS,
        speculative,
        playbackAtStart:this.utterancePlayback,
        playbackOriginAtStart:this.utterancePlaybackOrigin,
      })
    }catch(cause){this.handlers.onError(cause instanceof Error?cause.message:String(cause))}
  }

  private resetUtterance():void{
    this.gate=new SpeechGate(this.gateConfig,this.patience)
    this.silero?.reset()
    this.utterance=[];this.utteranceId='';this.utterancePlayback=false;this.utterancePlaybackOrigin=null
    this.bargeInFrames=[];this.bargeInConfirmed=false
    this.confirmedPlaybackOrigin=null
  }
}
