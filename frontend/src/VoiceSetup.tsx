import { rich, t } from './localization/index.ts'
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
    if((draft.summaries||draft.assistant)&&!modelReady)throw new Error(t("VoiceSetup.c5aaf01495"))
    await api('PATCH','/api/config',{tts_enabled:!!draft.read_aloud,stt_enabled:!!draft.dictation,tts_engine:draft.tts_engine||'sapi',stt_engine:draft.stt_engine||'whisper',...(draft.summaries?{}:{tts_content:'verbatim'}),...(draft.assistant?{}:{assistant_enabled:false})},{timeoutMs:15000})
    const features=[...(draft.summaries?['summaries']:[]),...(draft.assistant?['assistant']:[])]
    if(features.length)await api('POST','/api/onboarding/features/activate',{features},{timeoutMs:15000})
    await refresh();change({step:'install'})
  })
  const speak=()=>perform(async()=>{
    setHeard(false);setPlayed(false);unlockPlayback()
    const clip=await api<{id:string}>('POST','/api/voice/speak',{get text() { return t("VoiceSetup.6aecb8bdd4") }},{timeoutMs:90000})
    await playClip(clip.id,null,'system');setPlayed(true)
  })
  const stopCapture=()=>{clearTimeout(captureTimer.current);capture.current?.stop();capture.current=null;setRecording(false)}
  const testDictation=async()=>{
    if(capture.current)return
    setError('');setTranscript('');setMatched(false);setRecording(true)
    const next=new PersistentVoiceCapture({
      onSpeechStart:()=>{},onSpeechEnd:()=>{},onSpeculative:()=>{},onSpeculativeAbort:()=>{},onDetector:()=>{},playbackActive:()=>false,
      onError:message=>{stopCapture();setError(message)},
      onCaptureStall:()=>{stopCapture();setError(t("VoiceSetup.eb482c188e"))},
      onUtterance:audio=>{
        stopCapture()
        void perform(async()=>{
          const controller=new AbortController();request.current=controller
          const timer=setTimeout(()=>controller.abort(),90000)
          try{
            const response=await fetch('/api/voice/transcribe',{method:'POST',headers:{'Content-Type':'audio/wav','X-Mux-Utterance-Id':`setup-${Date.now()}`},body:audio,signal:controller.signal})
            const result=await response.json()
            if(!response.ok)throw new Error(result.error||t("VoiceSetup.fa6296f725"))
            if(!result.text?.trim())throw new Error(t("VoiceSetup.e39f2524ea"))
            if(live.current)setTranscript(result.text)
          }finally{clearTimeout(timer);request.current=null}
        })
      },
    })
    capture.current=next
    try{
      await next.start()
      if(!live.current||capture.current!==next){next.stop();return}
      captureTimer.current=setTimeout(()=>{stopCapture();setError(t("VoiceSetup.82c89bb37c"))},30000)
    }catch(cause){next.stop();stopCapture();setError(captureFailureNote((cause as Error).message,desktopMediaReport()))}
  }
  const hasCatalog=provider?.providers.find(item=>item.active)?.verification.capabilities.catalog!=='none'
  const override=config?customProviderOverride(config,hasCatalog):null
  const content=step==='provider'?<ProviderSetup savedDraft={providerDraft} onDraft={onProviderDraft} onBusy={setBusy} onReady={async()=>{await refresh();change({step:'choices'})}} onLater={async()=>{change({step:'choices'})}}/>:<section><fieldset class="setup-fields" disabled={busy}>
    <h2>{step==='choices' ? t("VoiceSetup.6db7eeeaf7") : step==='install' ? t("VoiceSetup.afa1abc489") : t("VoiceSetup.24fa91a93f")}</h2>
    {step==='choices' && <>
      <p class="setup-lede">{t("VoiceSetup.9035ddcb30")}</p>
      <label class="setup-voice-choice"><input type="checkbox" checked={!!draft.read_aloud} onChange={event=>change({read_aloud:event.currentTarget.checked,summaries:event.currentTarget.checked?draft.summaries:false})}/><span><strong>{t("VoiceSetup.462e512a3f")}</strong><small>{t("VoiceSetup.2dd5113d70")}</small></span></label>
      {draft.read_aloud && <div class="setup-cards">
        <label class={`setup-card ${draft.tts_engine==='sapi'?'selected':''}`}><input type="radio" name="tts-engine" disabled={!status?.providers.sapi?.available} checked={draft.tts_engine==='sapi'} onChange={()=>change({tts_engine:'sapi'})}/><span><strong>{t("VoiceSetup.12f2fd5a12")}</strong><small>{status?.providers.sapi?.available ? t("VoiceSetup.5c439785bc") : t("VoiceSetup.4b33228cb8")}</small></span></label>
        <label class={`setup-card ${draft.tts_engine==='kokoro'?'selected':''}`}><input type="radio" name="tts-engine" checked={draft.tts_engine==='kokoro'} onChange={()=>change({tts_engine:'kokoro'})}/><span><strong>{t("VoiceSetup.01eeccbc1e")}</strong><small>{t("VoiceSetup.02151217db")}</small></span></label>
      </div>}
      <label class="setup-voice-choice"><input type="checkbox" checked={!!draft.dictation} onChange={event=>change({dictation:event.currentTarget.checked,assistant:event.currentTarget.checked?draft.assistant:false})}/><span><strong>{t("VoiceSetup.39bcdd57e3")}</strong><small>{t("VoiceSetup.72aadc4714")}</small></span></label>
      {draft.dictation && <label class="setup-select">{rich("VoiceSetup.e62adbc7f6", { value1: <select value={draft.stt_engine||'whisper'} onChange={event=>change({stt_engine:event.currentTarget.value as 'sapi'|'whisper'})}><option value="whisper">{t("VoiceSetup.ea3eaae77d")}</option>{status?.providers.sapi?.available && <option value="sapi">{t("VoiceSetup.d8b9bd834e")}</option>}</select> })}</label>}
      <h3 data-heading-message="VoiceSetup.ab1bd8fe53">{t("VoiceSetup.ab1bd8fe53")}</h3>
      <label class="check">{rich("VoiceSetup.7a21fea5b7", { value1: <input type="checkbox" disabled={!modelReady} checked={!!draft.summaries} onChange={event=>change({summaries:event.currentTarget.checked,...(event.currentTarget.checked?{read_aloud:true}:{})})}/> })}</label>
      <label class="check">{rich("VoiceSetup.7c09dd8640", { value1: <input type="checkbox" disabled={!modelReady} checked={!!draft.assistant} onChange={event=>change({assistant:event.currentTarget.checked,...(event.currentTarget.checked?{dictation:true}:{})})}/> })}</label>
      {modelReady&&config ? <><SetupModelRow label={t("VoiceSetup.87bc590e04")} model={override?.model||config.tts_summary_model||config.openrouter_cheap_model} catalog={provider?.models.models||[]}/><SetupModelRow label={t("heading.0fe741492b")} model={override?.model||config.assistant_model} catalog={provider?.models.models||[]}/><button class="link" onClick={()=>change({step:'provider'})}>{t("VoiceSetup.a69d504555")}</button></> : <p class="setup-hint">{rich("VoiceSetup.c7015191b0", { value1: <button onClick={()=>change({step:'provider'})}>{t("VoiceSetup.8cd1856b03")}</button> })}</p>}
      <footer class="setup-step-actions"><button disabled={busy||!status||!selected} class="primary" onClick={()=>void configure()}>{t("VoiceSetup.31fbef1625")}</button></footer>
    </>}
    {step==='install' && <>
      {draft.read_aloud && <><h3 data-heading-message="heading.0791ec6847">{t("heading.0791ec6847")}</h3>{draft.tts_engine==='kokoro' ? <KokoroModelPanel initial={status?.kokoro_model||null}/> : <p>{readReady ? t("VoiceSetup.f2204fdfa7") : status?.providers.sapi?.diagnostic || t("VoiceSetup.9a60886c0c")}</p>}</>}
      {draft.dictation && <><h3 data-heading-message="VoiceSetup.526c70b826">{t("VoiceSetup.526c70b826")}</h3>{draft.stt_engine==='whisper' ? <WhisperModelPanel initial={status?.stt_models||null} runtime={status?.voice_runtime||null}/> : <p>{micReady ? t("VoiceSetup.38436df637") : status?.stt_diagnostic || t("VoiceSetup.b4bdf59df4")}</p>}</>}
      <p class="setup-hint">{t("VoiceSetup.ce22bfa00a")}</p>
      <footer class="setup-step-actions"><button disabled={busy} onClick={()=>change({step:'choices'})}>{t("VoiceSetup.76900f1bfd")}</button><button class="primary" disabled={busy||!selected||!readReady||!micReady} onClick={()=>change({step:'test'})}>{t("VoiceSetup.36d83c8ad6")}</button></footer>
    </>}
    {step==='test' && <>
      {draft.read_aloud && <div class="setup-voice-test"><h3 data-heading-message="heading.0791ec6847">{t("heading.0791ec6847")}</h3><button disabled={busy||recording||!readReady} onClick={()=>void speak()}>{t("VoiceSetup.b61f021842")}</button><label class="check">{rich("VoiceSetup.f1dbd158a7", { value1: <input type="checkbox" disabled={!played} checked={heard} onChange={event=>setHeard(event.currentTarget.checked)}/> })}</label></div>}
      {draft.dictation && <div class="setup-voice-test"><h3 data-heading-message="VoiceSetup.25ce1a09dd">{t("VoiceSetup.25ce1a09dd")}</h3><p class="setup-hint">{t("VoiceSetup.0c7a78ae0c")}</p>{!conversationCapability().available && <p role="alert">{conversationCapability().reason}</p>}<button disabled={busy||!micReady||!conversationCapability().available} onClick={()=>recording?stopCapture():void testDictation()}>{recording ? t("VoiceSetup.c123135fa3") : t("VoiceSetup.38038c6c69")}</button>{recording && <p role="status">{t("VoiceSetup.bbb4106e81")}</p>}{transcript && <><blockquote>{transcript}</blockquote><label class="check">{rich("VoiceSetup.fe1ffeb40b", { value1: <input type="checkbox" checked={matched} onChange={event=>setMatched(event.currentTarget.checked)}/> })}</label></>}</div>}
      {!canFinish && <p class="setup-hint">{t("VoiceSetup.a2b48e2eb7")}</p>}
      <p class="setup-hint">{draft.read_aloud && t("VoiceSetup.7f31f3bedd")}{draft.dictation && t("VoiceSetup.b8d8b5af05")}</p>
      <footer class="setup-step-actions"><button disabled={busy||recording} onClick={()=>change({step:'choices'})}>{t("VoiceSetup.76900f1bfd")}</button><button class="primary" disabled={busy||recording||!canFinish} onClick={()=>void perform(async()=>{if(onComplete)await onComplete();else onClose()})}>{t("VoiceSetup.708320e9d9")}</button></footer>
    </>}
    {error && <p role="alert">{error} <button disabled={busy} onClick={()=>void refresh().catch(cause=>setError(cause.message))}>{t("VoiceSetup.fb7099ad8e")}</button></p>}
  </fieldset></section>
  return embedded?content:<SetupGuide title={t("VoiceSetup.a430e6d293")} label={t("VoiceSetup.35caa7c79c")} busy={busy||recording} onClose={onClose}>{content}</SetupGuide>
}
