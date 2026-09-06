import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { conversationCapability, PersistentVoiceCapture } from './conversation'
import { captureFailureNote, desktopMediaReport } from './desktopShell'
import { KokoroModelPanel, WhisperModelPanel, type KokoroModelInfo, type VoiceRuntimeInfo, type WhisperModelInfo } from './voiceModelPanels'
import { playClip, unlockPlayback } from './voice'
import { ProviderSetup } from './ProviderSetup'
import { SetupGuide } from './SetupGuide'
import { SetupModelRow } from './SetupModelSummary'
import { customProviderOverride, type ModelRoutingConfig } from './modelRouting'
import { LLM_PROVIDER_CHANGED, type ProviderStatusPayload } from './llmProvider'
import type { ProviderSetupDraft, VoiceSetupDraft } from './onboarding'

type VoiceStatus={providers:Record<string,{available:boolean;diagnostic?:string}>;engine:string;stt_engine:string;stt_available:boolean;stt_diagnostic?:string;kokoro_model:KokoroModelInfo;voice_runtime:VoiceRuntimeInfo;stt_models:WhisperModelInfo[]}
type VoiceConfig=ModelRoutingConfig&{tts_enabled:boolean;stt_enabled:boolean;tts_engine:'sapi'|'kokoro'|'edge';stt_engine:'sapi'|'whisper';tts_content:string;assistant_enabled:boolean;llm_provider:string;custom_llm_model:string}
type Props={onClose:()=>void;onComplete?:()=>Promise<void>;draft?:VoiceSetupDraft;onDraft?:(draft:VoiceSetupDraft)=>void;onBusy?:(busy:boolean)=>void;embedded?:boolean;providerDraft?:ProviderSetupDraft;onProviderDraft?:(draft:ProviderSetupDraft)=>void}

export function VoiceSetup({onClose,onComplete,draft:stored,onDraft,onBusy,embedded=false,providerDraft,onProviderDraft}:Props) {
  const [draft,setDraft]=useState<VoiceSetupDraft>(stored||{})
  const [status,setStatus]=useState<VoiceStatus|null>(null)
  const [config,setConfig]=useState<VoiceConfig|null>(null)
  const [provider,setProvider]=useState<ProviderStatusPayload|null>(null)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [played,setPlayed]=useState(false)
  const [heard,setHeard]=useState(false)
  const [transcript,setTranscript]=useState('')
  const [matched,setMatched]=useState(false)
  const [recording,setRecording]=useState(false)
  const capture=useRef<PersistentVoiceCapture|null>(null)
  const request=useRef<AbortController|null>(null)
  const captureTimer=useRef<ReturnType<typeof setTimeout>>()
  const live=useRef(true)
  const refreshing=useRef<Promise<void>|null>(null)
  const providerLoading=useRef(false)
  const draftRef=useRef(draft)
  const change=(patch:Partial<VoiceSetupDraft>)=>{
    const next={...draftRef.current,...patch};draftRef.current=next;setDraft(next);onDraft?.(next)
    setHeard(false);setPlayed(false);setTranscript('');setMatched(false)
  }
  const refresh=()=>{
    // Provider availability never holds local speech setup behind a network check.
    if(!providerLoading.current){
      providerLoading.current=true
      void api<ProviderStatusPayload>('GET','/api/automation/provider',undefined,{timeoutMs:15000}).then(value=>{if(live.current)setProvider(value)}).catch(()=>{if(live.current)setProvider(null)}).finally(()=>{providerLoading.current=false})
    }
    if(refreshing.current)return refreshing.current
    const work=(async()=>{
      const [voice,cfg]=await Promise.all([api<VoiceStatus>('GET','/api/voice',undefined,{timeoutMs:12000}),api<VoiceConfig>('GET','/api/config',undefined,{timeoutMs:10000})])
      if(!live.current)return
      setStatus(voice);setConfig(cfg)
      if(draftRef.current.read_aloud===undefined)change({step:'choices',read_aloud:true,dictation:cfg.stt_enabled,tts_engine:cfg.tts_engine==='kokoro'||!voice.providers.sapi?.available?'kokoro':'sapi',stt_engine:cfg.stt_engine==='sapi'&&!voice.providers.sapi?.available?'whisper':cfg.stt_engine,summaries:cfg.tts_enabled&&cfg.tts_content==='summary',assistant:cfg.assistant_enabled})
    })()
    refreshing.current=work
    void work.finally(()=>{refreshing.current=null}).catch(()=>{})
    return work
  }
  useEffect(()=>{live.current=true;void refresh().catch(cause=>setError(cause.message));const changed=()=>void refresh().catch(cause=>setError(cause.message));window.addEventListener(LLM_PROVIDER_CHANGED,changed);return()=>{live.current=false;clearTimeout(captureTimer.current);capture.current?.stop();request.current?.abort();window.removeEventListener(LLM_PROVIDER_CHANGED,changed)}},[])
  useEffect(()=>{onBusy?.(busy||recording);return()=>onBusy?.(false)},[busy,recording])
  const step=draft.step||'choices'
  useEffect(()=>{
    if(step!=='install'&&step!=='test')return
    const timer=setInterval(()=>void refresh().catch(cause=>setError(cause.message)),2500)
    return()=>clearInterval(timer)
  },[step])
  const modelReady=provider?.activation?.ready===true
  const readReady=!draft.read_aloud||(status?.engine===(draft.tts_engine||'sapi')&&!!status?.providers[draft.tts_engine||'sapi']?.available)
  const micReady=!draft.dictation||(status?.stt_engine===(draft.stt_engine||'whisper')&&status.stt_available)
  const selected=!!(draft.read_aloud||draft.dictation)
  const canFinish=selected&&readReady&&micReady&&(!draft.read_aloud||heard)&&(!draft.dictation||matched)&&(!(draft.summaries||draft.assistant)||modelReady)
  const perform=async(action:()=>Promise<void>)=>{setBusy(true);setError('');try{await action()}catch(cause){if(live.current)setError((cause as Error).message)}finally{if(live.current)setBusy(false)}}
  const configure=()=>perform(async()=>{
    if((draft.summaries||draft.assistant)&&!modelReady)throw new Error('Add and verify a model provider, or keep local voice.')
    await api('PATCH','/api/config',{tts_enabled:!!draft.read_aloud,stt_enabled:!!draft.dictation,tts_engine:draft.tts_engine||'sapi',stt_engine:draft.stt_engine||'whisper',...(draft.summaries?{}:{tts_content:'verbatim'}),...(draft.assistant?{}:{assistant_enabled:false})},{timeoutMs:15000})
    const features=[...(draft.summaries?['summaries']:[]),...(draft.assistant?['assistant']:[])]
    if(features.length)await api('POST','/api/onboarding/features/activate',{features},{timeoutMs:15000})
    await refresh();change({step:'install'})
  })
  const speak=()=>perform(async()=>{
    setHeard(false);setPlayed(false);unlockPlayback()
    const clip=await api<{id:string}>('POST','/api/voice/speak',{text:'This is the voice swe-mux will use to read your replies.'},{timeoutMs:90000})
    await playClip(clip.id,null,'system');setPlayed(true)
  })
  const stopCapture=()=>{clearTimeout(captureTimer.current);capture.current?.stop();capture.current=null;setRecording(false)}
  const testDictation=async()=>{
    if(capture.current)return
    setError('');setTranscript('');setMatched(false);setRecording(true)
    const next=new PersistentVoiceCapture({
      onSpeechStart:()=>{},onSpeechEnd:()=>{},onSpeculative:()=>{},onSpeculativeAbort:()=>{},onDetector:()=>{},playbackActive:()=>false,
      onError:message=>{stopCapture();setError(message)},
      onCaptureStall:()=>{stopCapture();setError('No microphone audio arrived. Check the device and try again.')},
      onUtterance:audio=>{
        stopCapture()
        void perform(async()=>{
          const controller=new AbortController();request.current=controller
          const timer=setTimeout(()=>controller.abort(),90000)
          try{
            const response=await fetch('/api/voice/transcribe',{method:'POST',headers:{'Content-Type':'audio/wav','X-Mux-Utterance-Id':`setup-${Date.now()}`},body:audio,signal:controller.signal})
            const result=await response.json()
            if(!response.ok)throw new Error(result.error||'Transcription failed.')
            if(!result.text?.trim())throw new Error('No words were recognized. Try another short sentence.')
            if(live.current)setTranscript(result.text)
          }finally{clearTimeout(timer);request.current=null}
        })
      },
    })
    capture.current=next
    try{
      await next.start()
      if(!live.current||capture.current!==next){next.stop();return}
      captureTimer.current=setTimeout(()=>{stopCapture();setError('No sentence captured. Check the microphone and retry.')},30000)
    }catch(cause){next.stop();stopCapture();setError(captureFailureNote((cause as Error).message,desktopMediaReport()))}
  }
  const hasCatalog=provider?.providers.find(item=>item.active)?.verification.capabilities.catalog!=='none'
  const override=config?customProviderOverride(config,hasCatalog):null
  const content=step==='provider'?<ProviderSetup savedDraft={providerDraft} onDraft={onProviderDraft} onBusy={setBusy} onReady={async()=>{await refresh();change({step:'choices'})}} onLater={async()=>{change({step:'choices'})}}/>:<section><fieldset class="setup-fields" disabled={busy}>
    <h2>{step==='choices'?'What would you like voice to do?':step==='install'?'Get voice ready.':'Try your voice setup.'}</h2>
    {step==='choices'&&<>
      <p class="setup-lede">Reading and dictation run on this computer.</p>
      <label class="setup-voice-choice"><input type="checkbox" checked={!!draft.read_aloud} onChange={event=>change({read_aloud:event.currentTarget.checked,summaries:event.currentTarget.checked?draft.summaries:false})}/><span><strong>Read replies aloud</strong><small>Hear replies in full, with no model API.</small></span></label>
      {draft.read_aloud&&<div class="setup-cards">
        <label class={`setup-card ${draft.tts_engine==='sapi'?'selected':''}`}><input type="radio" name="tts-engine" disabled={!status?.providers.sapi?.available} checked={draft.tts_engine==='sapi'} onChange={()=>change({tts_engine:'sapi'})}/><span><strong>Built-in voice</strong><small>{status?.providers.sapi?.available?'Ready now. No download.':'Available on Windows with PowerShell.'}</small></span></label>
        <label class={`setup-card ${draft.tts_engine==='kokoro'?'selected':''}`}><input type="radio" name="tts-engine" checked={draft.tts_engine==='kokoro'} onChange={()=>change({tts_engine:'kokoro'})}/><span><strong>Local neural voice</strong><small>More natural speech. Download and install in the next step.</small></span></label>
      </div>}
      <label class="setup-voice-choice"><input type="checkbox" checked={!!draft.dictation} onChange={event=>change({dictation:event.currentTarget.checked,assistant:event.currentTarget.checked?draft.assistant:false})}/><span><strong>Dictate and use voice commands</strong><small>Speak into the workspace with local speech recognition.</small></span></label>
      {draft.dictation&&<label class="setup-select">Speech recognition<select value={draft.stt_engine||'whisper'} onChange={event=>change({stt_engine:event.currentTarget.value as 'sapi'|'whisper'})}><option value="whisper">Local Whisper models</option>{status?.providers.sapi?.available&&<option value="sapi">Windows Speech Recognition</option>}</select></label>}
      <h3>Optional AI features</h3>
      <label class="check"><input type="checkbox" disabled={!modelReady} checked={!!draft.summaries} onChange={event=>change({summaries:event.currentTarget.checked,...(event.currentTarget.checked?{read_aloud:true}:{})})}/>Summarize long replies before reading</label>
      <label class="check"><input type="checkbox" disabled={!modelReady} checked={!!draft.assistant} onChange={event=>change({assistant:event.currentTarget.checked,...(event.currentTarget.checked?{dictation:true}:{})})}/>Talk with the Mux assistant</label>
      {modelReady&&config?<><SetupModelRow label="Summaries" model={override?.model||config.tts_summary_model||config.openrouter_cheap_model} catalog={provider?.models.models||[]}/><SetupModelRow label="Mux assistant" model={override?.model||config.assistant_model} catalog={provider?.models.models||[]}/><button class="link" onClick={()=>change({step:'provider'})}>Change provider or models…</button></>:<p class="setup-hint">Add a model provider to enable AI summaries and conversation. <button onClick={()=>change({step:'provider'})}>Add provider</button> You can keep local voice.</p>}
      <footer class="setup-step-actions"><button disabled={busy||!status||!selected} class="primary" onClick={()=>void configure()}>Continue</button></footer>
    </>}
    {step==='install'&&<>
      {draft.read_aloud&&<><h3>Read aloud</h3>{draft.tts_engine==='kokoro'?<KokoroModelPanel initial={status?.kokoro_model||null}/>:<p>{readReady?'Built-in voice is ready.':status?.providers.sapi?.diagnostic||'Checking built-in voice…'}</p>}</>}
      {draft.dictation&&<><h3>Speech recognition</h3>{draft.stt_engine==='whisper'?<WhisperModelPanel initial={status?.stt_models||null} runtime={status?.voice_runtime||null}/>:<p>{micReady?'Windows Speech Recognition is ready.':status?.stt_diagnostic||'Checking speech recognition…'}</p>}</>}
      <p class="setup-hint">Downloads start only when you press Download. Retry any failed part here.</p>
      <footer class="setup-step-actions"><button disabled={busy} onClick={()=>change({step:'choices'})}>Back</button><button class="primary" disabled={busy||!selected||!readReady||!micReady} onClick={()=>change({step:'test'})}>Test voice</button></footer>
    </>}
    {step==='test'&&<>
      {draft.read_aloud&&<div class="setup-voice-test"><h3>Read aloud</h3><button disabled={busy||recording||!readReady} onClick={()=>void speak()}>Speak a test sentence</button><label class="check"><input type="checkbox" disabled={!played} checked={heard} onChange={event=>setHeard(event.currentTarget.checked)}/>I heard the sentence</label></div>}
      {draft.dictation&&<div class="setup-voice-test"><h3>Dictation</h3><p class="setup-hint">Say a short sentence, then pause. Nothing is sent to an agent.</p>{!conversationCapability().available&&<p role="alert">{conversationCapability().reason}</p>}<button disabled={busy||!micReady||!conversationCapability().available} onClick={()=>recording?stopCapture():void testDictation()}>{recording?'Stop microphone test':'Test microphone and dictation'}</button>{recording&&<p role="status">Listening…</p>}{transcript&&<><blockquote>{transcript}</blockquote><label class="check"><input type="checkbox" checked={matched} onChange={event=>setMatched(event.currentTarget.checked)}/>That matches what I said</label></>}</div>}
      {!canFinish&&<p class="setup-hint">Confirm each selected capability works, or go Back to change your choices.</p>}
      <p class="setup-hint">{draft.read_aloud&&'Use each session’s voice controls for automatic reading. '}{draft.dictation&&'Use Talk in the workspace to dictate.'}</p>
      <footer class="setup-step-actions"><button disabled={busy||recording} onClick={()=>change({step:'choices'})}>Back</button><button class="primary" disabled={busy||recording||!canFinish} onClick={()=>void perform(async()=>{if(onComplete)await onComplete();else onClose()})}>Finish voice setup</button></footer>
    </>}
    {error&&<p role="alert">{error} <button disabled={busy} onClick={()=>void refresh().catch(cause=>setError(cause.message))}>Check again</button></p>}
  </fieldset></section>
  return embedded?content:<SetupGuide title="VOICE" label="Set up voice" busy={busy||recording} onClose={onClose}>{content}</SetupGuide>
}
