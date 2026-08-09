import { FrameAssembler, frameRms, joinFrames, StreamingDownsampler, VAD_FRAME_MS, VAD_SAMPLE_RATE } from './audioFrames.ts'
import { SileroVad } from './sileroVad.ts'
import { ENERGY_GATE, SILERO_GATE, SpeechGate } from './speechGate.ts'
import type { GateConfig } from './speechGate'
import { CAPTURE_WORKLET_NAME, captureWorkletUrl } from './voiceCaptureWorklet.ts'
import type { CaptureMarks } from './voiceLatency'

export type MuxVoiceCommand='send'|'append'|'cancel'|'undo'|'mute'|'read'|'summary'|'verbatim'|'help'|'stop'|'interrupt'|'standby'|'resume'|'comms_on'|'comms_off'
export type ParsedMuxVoice={command:MuxVoiceCommand|null;text:string}
export type VoiceCommandConfig={action:string;phrases:string[]}
export type VoiceMatcher={parse(text:string):ParsedMuxVoice}

// The client executes this fixed action set; wake words and the phrases mapped to
// each action are configurable (daemon config, surfaced via /api/voice). Keep in
// sync with VOICE_COMMAND_ACTIONS / default_voice_commands in config.py — these
// defaults are only the fallback when the daemon has not supplied a config yet.
const VOICE_ACTIONS=new Set<MuxVoiceCommand>(['send','append','cancel','undo','mute','read','summary','verbatim','interrupt','help','standby','resume','stop','comms_on','comms_off'])
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
  {action:'comms_on',phrases:['voice comms','voice comms on','start voice comms','enter voice comms']},
  {action:'comms_off',phrases:['voice comms off','stop voice comms','exit voice comms']},
  {action:'stop',phrases:['stop listening','turn off','shut down']},
]

const normalizePhrase=(value:string):string=>value.replace(/\s+/g,' ').trim().toLowerCase()
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

/** Reject likely speaker echo without closing the microphone during playback. */
export function playbackSafeProbability(probability:number,rms:number,playing:boolean):number{
  if(!playing)return probability
  return probability>=.8&&rms>=.035?probability:0
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
  playbackActive():boolean
  playbackOrigin?():'agent'|'system'|null
}

const newUtteranceId=():string=>globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(36).slice(2)}`

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
  private gate=new SpeechGate(ENERGY_GATE)
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
  private pending:Promise<void>=Promise.resolve()
  private stopped=false
  /** Push-to-talk suspends endpointing entirely; the key release is the endpoint. */
  private pushToTalk=false

  constructor(handlers:CaptureHandlers){this.handlers=handlers}

  async start():Promise<void>{
    if(this.context)return
    const capability=conversationCapability()
    if(!capability.available)throw new Error(capability.reason)
    this.stopped=false
    this.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false})
    this.context=new AudioContext({latencyHint:'interactive'})
    await this.context.resume()
    this.resampler=new StreamingDownsampler(this.context.sampleRate)
    this.source=this.context.createMediaStreamSource(this.stream)
    await this.attachSource()
    // Started after capture is live rather than before it: the model is 2.3 MB over
    // whatever link a phone is on, and blocking the microphone on that download would
    // make Talk look broken for seconds. The energy detector is already running until
    // it lands, so no speech is lost while it downloads.
    void this.loadSilero()
  }

  stop():void{
    this.stopped=true
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
      if(!this.gate.speaking)this.gate=new SpeechGate(SILERO_GATE)
      this.handlers.onDetector('silero','Silero voice activity detection; utterances end after a short pause.')
    }catch(cause){
      silero.dispose()
      const reason=cause instanceof Error?cause.message:String(cause)
      this.handlers.onDetector('energy',`Silero VAD unavailable (${reason}); using the energy detector, which needs a longer pause.`)
    }
  }

  /** Resample and frame one block of microphone audio, then queue it for detection. */
  private ingest(block:Float32Array):void{
    const resampler=this.resampler
    if(!resampler||this.stopped)return
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
    if(this.detector==='silero'&&silero?.ready){
      return playbackSafeProbability(await silero.probability(frame),rms,this.handlers.playbackActive())
    }
    // Energy fallback, reduced to the same 0/1 signal the gate consumes. The raised
    // floor during playback is what keeps the agent's own read-aloud from
    // self-triggering the microphone.
    if(!this.gate.speaking)this.noiseFloor=this.noiseFloor*.97+Math.min(rms,.04)*.03
    const threshold=Math.max(.012,this.noiseFloor*3.2,this.handlers.playbackActive()?.035:0)
    return rms>threshold?1:0
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
        this.utterancePlayback=this.handlers.playbackActive()
        this.utterancePlaybackOrigin=this.handlers.playbackOrigin?.()||null
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
    this.gate=new SpeechGate(this.gateConfig)
    this.silero?.reset()
    this.utterance=[];this.utteranceId='';this.utterancePlayback=false;this.utterancePlaybackOrigin=null
    this.bargeInFrames=[];this.bargeInConfirmed=false
  }
}
