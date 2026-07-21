export type MuxVoiceCommand='send'|'cancel'|'undo'|'mute'|'read'|'summary'|'verbatim'|'help'|'stop'|'interrupt'
export type ParsedMuxVoice={command:MuxVoiceCommand|null;text:string}

// Mux is the displayed wake word. Keep close phonetic spellings because speech
// recognizers occasionally render the product name as "mucks" or "max".
const MUX_COMMAND=/\b(?:hey\s+|okay\s+)?(?:mux|mucks|max)\s*[,;:\-]?\s*(send(?:\s+(?:it|that|message))?|submit(?:\s+(?:it|that|message))?|cancel(?:\s+that)?|clear(?:\s+that)?|undo(?:\s+(?:that|last(?:\s+(?:phrase|message))?))?|delete\s+last(?:\s+(?:phrase|message))?|mute|stop\s+(?:speaking|playback|audio)|read(?:\s+(?:the\s+)?(?:reply|response)(?:\s+again)?)?|speak\s+(?:the\s+)?(?:reply|response)|summary(?:\s+mode)?|use\s+summaries|verbatim(?:\s+mode)?|read\s+verbatim|help|list\s+commands|what\s+can\s+i\s+say|stop\s+listening|go\s+to\s+sleep|sleep|interrupt(?:\s+(?:the\s+)?agent)?)\s*[.!?]*$/i

export function parseMuxVoice(text:string):ParsedMuxVoice{
  const cleaned=text.replace(/\s+/g,' ').trim()
  const match=MUX_COMMAND.exec(cleaned)
  if(!match)return {command:null,text:cleaned}
  const phrase=match[1].toLowerCase()
  const command:MuxVoiceCommand=phrase.startsWith('send')||phrase.startsWith('submit')?'send'
    :phrase.startsWith('cancel')||phrase.startsWith('clear')?'cancel'
    :phrase.startsWith('undo')||phrase.startsWith('delete last')?'undo'
    :phrase==='mute'||phrase.startsWith('stop speaking')||phrase.startsWith('stop playback')||phrase.startsWith('stop audio')?'mute'
    :phrase.includes('summary')||phrase==='use summaries'?'summary'
    :phrase.includes('verbatim')?'verbatim'
    :phrase==='help'||phrase==='list commands'||phrase==='what can i say'?'help'
    :phrase.startsWith('read')||phrase.startsWith('speak')?'read'
    :phrase.startsWith('interrupt')?'interrupt':'stop'
  return {command,text:cleaned.slice(0,match.index).trim()}
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
