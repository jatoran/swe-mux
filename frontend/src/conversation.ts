export type MuxVoiceCommand='send'|'cancel'|'undo'|'mute'|'read'|'summary'|'verbatim'|'help'|'stop'|'interrupt'|'standby'|'resume'
export type ParsedMuxVoice={command:MuxVoiceCommand|null;text:string}
export type VoiceCommandConfig={action:string;phrases:string[]}
export type VoiceMatcher={parse(text:string):ParsedMuxVoice}

// The client executes this fixed action set; wake words and the phrases mapped to
// each action are configurable (daemon config, surfaced via /api/voice). Keep in
// sync with VOICE_COMMAND_ACTIONS / default_voice_commands in config.py — these
// defaults are only the fallback when the daemon has not supplied a config yet.
const VOICE_ACTIONS=new Set<MuxVoiceCommand>(['send','cancel','undo','mute','read','summary','verbatim','interrupt','help','standby','resume','stop'])
export const DEFAULT_WAKE_WORDS=['mux','mucks','max']
export const DEFAULT_COMMANDS:VoiceCommandConfig[]=[
  {action:'send',phrases:['send','send it','send that','send message','submit','submit it','submit that','submit message']},
  {action:'cancel',phrases:['cancel','cancel that','clear','clear that']},
  {action:'undo',phrases:['undo','undo that','undo last','undo last phrase','delete last','delete last phrase']},
  {action:'mute',phrases:['mute','stop speaking','stop playback','stop audio']},
  {action:'read',phrases:['read','read reply','read the reply','read reply again','read the reply again','read response','speak reply','speak the reply']},
  {action:'summary',phrases:['summary','summary mode','use summaries']},
  {action:'verbatim',phrases:['verbatim','verbatim mode','read verbatim']},
  {action:'interrupt',phrases:['interrupt','interrupt agent','interrupt the agent']},
  {action:'help',phrases:['help','list commands','what can i say']},
  {action:'standby',phrases:['sleep','go to sleep','stand by','standby','pause listening']},
  {action:'resume',phrases:['wake','wake up','resume','start listening']},
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

export type ConversationCapability={available:boolean;secureContext:boolean;mediaDevices:boolean;audioContext:boolean;reason:string}

export function conversationCapability():ConversationCapability{
  const secureContext=window.isSecureContext
  const mediaDevices=!!navigator.mediaDevices?.getUserMedia
  const audioContext=typeof AudioContext!=='undefined'
  return {available:secureContext&&mediaDevices&&audioContext,secureContext,mediaDevices,audioContext,
    reason:!secureContext?'HTTPS or localhost is required.':!mediaDevices?'Microphone capture is unavailable in this browser.':!audioContext?'Web Audio is unavailable in this browser.':'Persistent swe-mux microphone capture is available.'}
}

export function downsample(input:Float32Array,inputRate:number,outputRate=16_000):Float32Array{
  if(outputRate>=inputRate)return input.slice()
  const ratio=inputRate/outputRate
  const length=Math.max(1,Math.round(input.length/ratio))
  const result=new Float32Array(length)
  let inputOffset=0
  for(let outputOffset=0;outputOffset<length;outputOffset++){
    const nextInputOffset=Math.min(input.length,Math.round((outputOffset+1)*ratio))
    let total=0,count=0
    for(;inputOffset<nextInputOffset;inputOffset++){total+=input[inputOffset];count++}
    result[outputOffset]=count?total/count:0
  }
  return result
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

type CaptureHandlers={
  onSpeechStart():void
  onUtterance(audio:Blob,durationMs:number):void
  onError(message:string):void
  playbackActive():boolean
}

export class PersistentVoiceCapture{
  private handlers:CaptureHandlers
  private context:AudioContext|null=null
  private stream:MediaStream|null=null
  private source:MediaStreamAudioSourceNode|null=null
  private processor:ScriptProcessorNode|null=null
  private sink:GainNode|null=null
  private preRoll:Float32Array[]=[]
  private utterance:Float32Array[]=[]
  private speaking=false
  private silenceMs=0
  private speechMs=0
  private totalMs=0
  private noiseFloor=0.004

  constructor(handlers:CaptureHandlers){this.handlers=handlers}

  async start():Promise<void>{
    if(this.context)return
    const capability=conversationCapability()
    if(!capability.available)throw new Error(capability.reason)
    this.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false})
    this.context=new AudioContext({latencyHint:'interactive'})
    await this.context.resume()
    this.source=this.context.createMediaStreamSource(this.stream)
    this.processor=this.context.createScriptProcessor(2048,1,1)
    this.sink=this.context.createGain();this.sink.gain.value=0
    this.source.connect(this.processor);this.processor.connect(this.sink);this.sink.connect(this.context.destination)
    this.processor.onaudioprocess=event=>this.process(event.inputBuffer.getChannelData(0))
  }

  stop():void{
    if(this.processor)this.processor.onaudioprocess=null
    try{this.source?.disconnect();this.processor?.disconnect();this.sink?.disconnect()}catch{/* already disconnected */}
    for(const track of this.stream?.getTracks()||[])track.stop()
    void this.context?.close()
    this.context=null;this.stream=null;this.source=null;this.processor=null;this.sink=null
    this.resetUtterance();this.preRoll=[]
  }

  private process(input:Float32Array):void{
    const chunk=input.slice()
    const durationMs=chunk.length/(this.context?.sampleRate||48_000)*1000
    let squareSum=0
    for(const sample of chunk)squareSum+=sample*sample
    const rms=Math.sqrt(squareSum/Math.max(1,chunk.length))
    if(!this.speaking)this.noiseFloor=this.noiseFloor*.97+Math.min(rms,.04)*.03
    const threshold=Math.max(.012,this.noiseFloor*3.2,this.handlers.playbackActive()?.035:0)
    if(!this.speaking){
      this.preRoll.push(chunk)
      let preRollMs=this.preRoll.reduce((total,item)=>total+item.length/(this.context?.sampleRate||48_000)*1000,0)
      while(preRollMs>320&&this.preRoll.length>2){
        const removed=this.preRoll.shift()
        if(removed)preRollMs-=removed.length/(this.context?.sampleRate||48_000)*1000
      }
      if(rms<=threshold)return
      this.speaking=true;this.silenceMs=0;this.speechMs=durationMs;this.totalMs=preRollMs
      this.utterance=this.preRoll.splice(0)
      this.handlers.onSpeechStart()
      return
    }
    this.utterance.push(chunk);this.totalMs+=durationMs
    if(rms>threshold){this.silenceMs=0;this.speechMs+=durationMs}else this.silenceMs+=durationMs
    if((this.silenceMs>=900&&this.speechMs>=220)||this.totalMs>=30_000)this.finish()
  }

  private finish():void{
    const context=this.context
    const chunks=this.utterance
    const durationMs=this.totalMs
    this.resetUtterance()
    if(!context||!chunks.length)return
    const length=chunks.reduce((total,item)=>total+item.length,0)
    const joined=new Float32Array(length)
    let offset=0
    for(const chunk of chunks){joined.set(chunk,offset);offset+=chunk.length}
    try{this.handlers.onUtterance(encodeWav(downsample(joined,context.sampleRate)),durationMs)}
    catch(cause){this.handlers.onError(cause instanceof Error?cause.message:String(cause))}
  }

  private resetUtterance():void{
    this.speaking=false;this.silenceMs=0;this.speechMs=0;this.totalMs=0;this.utterance=[]
  }
}
