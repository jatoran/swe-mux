/**
 * The first-use asset panels shared by Settings and the guided voice setup.
 *
 * They live here rather than in `Settings.tsx` for a measured reason, not a
 * tidiness one. `VoiceSetup` imported two of them *by value*, which is a static
 * edge into a 3,000-line module - so the whole Settings panel was pulled into
 * the workspace's main chunk and had to be downloaded and parsed before the
 * sidebar could draw. Cutting that edge moved 220 KiB (68 KiB gzipped) out of
 * the main bundle, measured 2026-08-30.
 *
 * One acquisition surface, two hosts: the rule these panels already carried,
 * now expressed as a module boundary instead of a cross-import.
 */
import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'

/**
 * The spaCy English model the Kokoro G2P loads before it can pronounce anything.
 * Its own state rather than a boolean on the weights, because `installed` (the
 * environment resolves the distribution - a source checkout, the desktop app)
 * and `downloaded` (this daemon fetched it into the data directory) are the same
 * working state reached two different ways, and only one of them is anything the
 * operator can act on.
 */
export type G2pModelInfo = {
  status:'not_downloaded'|'downloading'|'ready'|'error'
  source?:'installed'|'downloaded'|null
  distribution:string;version:string
  total_bytes:number;downloaded_bytes:number;error?:string|null
}
export type KokoroModelInfo = {
  status:'not_downloaded'|'downloading'|'ready'|'error'
  total_bytes:number;downloaded_bytes:number;current_file?:string|null
  error?:string|null;voices:string[];g2p?:G2pModelInfo;runtime?:VoiceRuntimeInfo
}
// The speech *libraries*, which the desktop bundle stopped carrying on
// 2026-08-29 (ROADMAP Phase 21 Workstream D). Same four states as every other
// first-use asset, plus `supported`: a platform the pinned closure has no wheels
// for is an absence no press can fix, and drawing it as `not_downloaded` beside
// a button would be an interface that lies.
export type VoiceRuntimeInfo = {
  status:'not_downloaded'|'downloading'|'ready'|'error'
  source?:'installed'|'downloaded'|null
  supported:boolean;closure:string;distributions:number
  total_bytes:number;downloaded_bytes:number;current_file?:string|null;error?:string|null
}
// Same four states as the Kokoro model, deliberately: every asset swe-mux fetches
// on demand reports one vocabulary. `backend_installed` is the *other* kind of
// absence — the `voice-local` extra itself missing — which no download fixes.
export type WhisperModelInfo = {
  model:string
  status:'not_downloaded'|'downloading'|'ready'|'error'
  backend_installed:boolean
  path?:string|null; size_hint?:string|null; approximate_mb?:number|null
  elapsed_seconds?:number|null; error?:string|null
}

/**
 * The on-device speech libraries, as a first-use asset with an explicit press.
 *
 * Rendered inside the dictation panel with `action={false}`, as a status line
 * with no button of its own. The Kokoro panel draws this same store itself, and
 * both presses acquire it: a control for it in a third place is what broke the
 * flow on 2026-08-29, when an operator pressed the Kokoro button, saw its bars
 * finish, and did not know a separate panel held a prerequisite. One press per
 * capability; the sub-steps are lines, not controls.
 *
 * `supported: false` renders as a statement with no button. The pinned closure
 * covers Windows, Linux and macOS on the architectures swe-mux ships for; an
 * interpreter outside that set has nothing to press, and its remedy is the
 * install-time extra rather than this panel.
 */
// The `voice_model_progress` payload, as the daemon now sends it: which store
// spoke, and that store's whole status nested rather than splatted. The flat
// shape it replaced passed a store's data into `EventBus.emit`'s parameters, and
// two of the four stores answer with a `source` key that collided with emit's
// own - which killed the download task and reported itself as an interrupted
// transfer (`routes/voice.emit_model_progress`).
type ModelProgressEvent = {model?:string;asset?:Record<string,unknown>}

function assetFor(raw:Event,model:string):Record<string,unknown>|null{
  const detail=(raw as CustomEvent).detail as ModelProgressEvent|undefined
  if(detail?.model!==model)return null
  const asset=detail.asset
  return asset&&typeof asset.status==='string'?asset:null
}

export function VoiceRuntimePanel({initial,action=true}:{initial:VoiceRuntimeInfo|null;action?:boolean}){
  const [runtime,setRuntime]=useState<VoiceRuntimeInfo|null>(initial)
  const [starting,setStarting]=useState(false)
  useEffect(()=>{
    setRuntime(initial)
    void api<VoiceRuntimeInfo>('GET','/api/voice/models/runtime').then(setRuntime).catch(()=>{})
  },[initial])
  useEffect(()=>{
    const handler=(raw:Event)=>{
      // Labelled events only, and matched on the label rather than on the
      // absence of one: two panels listen to this stream and a claim on the
      // wrong event overwrites a live download's progress with another's total.
      const asset=assetFor(raw,'runtime')
      if(!asset)return
      setRuntime(current=>({...(current||{supported:true,closure:'',distributions:0,total_bytes:0,downloaded_bytes:0}),...asset} as VoiceRuntimeInfo))
    }
    window.addEventListener('mux:voice-model',handler)
    return()=>window.removeEventListener('mux:voice-model',handler)
  },[])
  useEffect(()=>{
    if(runtime?.status!=='downloading')return
    const timer=setInterval(()=>{void api<VoiceRuntimeInfo>('GET','/api/voice/models/runtime').then(setRuntime).catch(()=>{})},2000)
    return()=>clearInterval(timer)
  },[runtime?.status])
  const download=async()=>{
    setStarting(true)
    try{const next=await api<VoiceRuntimeInfo&{started:boolean}>('POST','/api/voice/models/runtime/download');setRuntime(next)}
    catch{/* surfaced by the next status refresh */}
    finally{setStarting(false)}
  }
  if(!runtime)return null
  const status=runtime.status
  const total=runtime.total_bytes||0
  const done=runtime.downloaded_bytes||0
  const pct=total?Math.min(100,Math.round(done/total*100)):0
  const megabytes=Math.round(total/1048576)
  const waiting=runtime.supported&&status!=='ready'&&status!=='downloading'
  return <div class="kokoro-model-panel">
    <p aria-live="polite">
      <span class={`state-dot ${status==='ready'?'idle':status==='downloading'?'running':'stopped'}`}/>
      Speech libraries::{runtime.supported?status:'unsupported'}
      {status==='ready'&&runtime.source==='installed'&&' · installed in this environment'}
      {status==='ready'&&runtime.source==='downloaded'&&` · ${runtime.distributions} packages, ${megabytes} MB, hash-verified`}
      {status==='downloading'&&` · ${pct}% (${Math.round(done/1048576)}/${megabytes} MB)${runtime.current_file?` · ${runtime.current_file}`:''}`}
      {runtime.supported&&status==='not_downloaded'&&' · read aloud and dictation both need them'}
      {(!runtime.supported||status==='error')&&runtime.error&&` · ${runtime.error}`}
    </p>
    {action&&waiting&&<button disabled={starting} onClick={()=>void download()}>{status==='error'?'Retry download':`Download speech libraries (~${megabytes} MB)`}</button>}
  </div>
}

// Exported for the guided voice setup (`VoiceSetup.tsx`), which reuses these
// panels rather than copying them - one acquisition surface, two hosts.
export function KokoroModelPanel({initial}:{initial:KokoroModelInfo|null}){
  const [model,setModel]=useState<KokoroModelInfo|null>(initial)
  const [starting,setStarting]=useState(false)
  // The panel is unmounted while another provider is selected. Re-read on each
  // mount so a download that progressed or finished while hidden returns with
  // its real state instead of the stale `/api/voice` snapshot from Settings open.
  useEffect(()=>{
    setModel(initial)
    void api<KokoroModelInfo>('GET','/api/voice/models/kokoro').then(setModel).catch(()=>{})
  },[initial])
  useEffect(()=>{
    const handler=(raw:Event)=>{
      // Three stores behind one press, three labels, three sub-objects. Every
      // branch matches its own label exactly; none of them accepts an unlabelled
      // event, which is what used to let a 12 MB companion's progress overwrite
      // a 106 MB download's mid-transfer.
      const g2pAsset=assetFor(raw,'g2p')
      if(g2pAsset){
        setModel(current=>current?{...current,g2p:{...(current.g2p as G2pModelInfo|undefined),...g2pAsset} as G2pModelInfo}:current)
        return
      }
      const runtimeAsset=assetFor(raw,'runtime')
      if(runtimeAsset){
        setModel(current=>current?{...current,runtime:{...(current.runtime as VoiceRuntimeInfo|undefined),...runtimeAsset} as VoiceRuntimeInfo}:current)
        return
      }
      const kokoroAsset=assetFor(raw,'kokoro')
      if(kokoroAsset)setModel(current=>({...(current||{total_bytes:0,downloaded_bytes:0,voices:[]}),...kokoroAsset} as KokoroModelInfo))
    }
    window.addEventListener('mux:voice-model',handler)
    return()=>window.removeEventListener('mux:voice-model',handler)
  },[])
  useEffect(()=>{
    if(model?.status!=='downloading')return
    const timer=setInterval(()=>{void api<KokoroModelInfo>('GET','/api/voice/models/kokoro').then(setModel).catch(()=>{})},2000)
    return()=>clearInterval(timer)
  },[model?.status])
  const download=async()=>{
    setStarting(true)
    try{const next=await api<KokoroModelInfo&{started:boolean}>('POST','/api/voice/models/kokoro/download');setModel(next)}
    catch{/* surfaced by the next status refresh */}
    finally{setStarting(false)}
  }
  const status=model?.status||'not_downloaded'
  const total=model?.total_bytes||0
  const done=model?.downloaded_bytes||0
  const pct=total?Math.min(100,Math.round(done/total*100)):0
  // Three parts of one capability, drawn as three lines behind one press. They
  // can fail independently, so a single merged bar would have to lie about which
  // one failed - but that is an argument for three *lines*, not three buttons,
  // and the two-button version failed a real operator on 2026-08-29: he pressed
  // the one here, watched both its bars finish, and met a 500 at the first
  // spoken sentence because the speech libraries had their own button in another
  // panel. The user is not the integrator of three stores.
  const g2p=model?.g2p
  const g2pStatus=g2p?.status||'not_downloaded'
  const g2pWaiting=Boolean(g2p)&&g2pStatus!=='ready'&&g2pStatus!=='downloading'
  const runtime=model?.runtime
  const runtimeStatus=runtime?runtime.supported?runtime.status:'unsupported':'ready'
  const runtimeWaiting=Boolean(runtime)&&runtime?.supported===true&&runtimeStatus!=='ready'&&runtimeStatus!=='downloading'
  const waiting=status!=='ready'&&status!=='downloading'
  const busy=status==='downloading'||g2pStatus==='downloading'||runtimeStatus==='downloading'
  const failed=status==='error'||g2pStatus==='error'||runtimeStatus==='error'
  // The size on the button is what this press is about to cost *now*, so a
  // partly-acquired install is not quoted the full figure it already paid.
  const outstanding=(runtimeWaiting?(runtime?.total_bytes||0):0)
    +(waiting?total:0)
    +(g2pWaiting?(g2p?.total_bytes||0):0)
  // One button, and it retries exactly what failed: every store's
  // `start_download` short-circuits when it is already `ready`, so pressing this
  // after a partial failure re-runs the failed parts and no others. That is the
  // per-store retry the three lines promise, without three controls to read.
  const label=failed?'Retry what failed'
    :`Download everything Kokoro needs (~${Math.max(1,Math.round(outstanding/1048576))} MB)`
  return <div class="kokoro-model-panel">
    {runtime&&<p aria-live="polite">
      <span class={`state-dot ${runtimeStatus==='ready'?'idle':runtimeStatus==='downloading'?'running':'stopped'}`}/>
      Speech libraries::{runtimeStatus}
      {runtimeStatus==='ready'&&runtime.source==='installed'&&' · installed in this environment'}
      {runtimeStatus==='ready'&&runtime.source!=='installed'&&` · ${runtime.distributions} packages, ${Math.round((runtime.total_bytes||0)/1048576)} MB, hash-verified`}
      {runtimeStatus==='downloading'&&` · ${Math.round((runtime.downloaded_bytes||0)/1048576)}/${Math.round((runtime.total_bytes||0)/1048576)} MB${runtime.current_file?` · ${runtime.current_file}`:''}`}
      {runtimeStatus==='not_downloaded'&&' · Kokoro has no engine to load without them'}
      {(runtimeStatus==='error'||runtimeStatus==='unsupported')&&runtime.error&&` · ${runtime.error}`}
    </p>}
    <p aria-live="polite">
      <span class={`state-dot ${status==='ready'?'idle':status==='downloading'?'running':'stopped'}`}/>
      Kokoro model::{status}
      {status==='downloading'&&` · ${pct}% (${Math.round(done/1048576)}/${Math.round(total/1048576)} MB)${model?.current_file?` · ${model.current_file}`:''}`}
      {status==='ready'&&` · ${Math.round(total/1048576)} MB, hash-verified`}
      {status==='error'&&model?.error&&` · ${model.error}`}
    </p>
    {g2p&&<p aria-live="polite">
      <span class={`state-dot ${g2pStatus==='ready'?'idle':g2pStatus==='downloading'?'running':'stopped'}`}/>
      Pronunciation model::{g2pStatus}
      {g2pStatus==='ready'&&` · ${g2p.distribution} ${g2p.version}, ${g2p.source==='installed'?'installed in this environment':'downloaded and hash-verified'}`}
      {g2pStatus==='downloading'&&` · ${Math.round((g2p.total_bytes||0)/1048576)} MB`}
      {g2pStatus==='not_downloaded'&&' · Kokoro cannot pronounce anything without it'}
      {g2pStatus==='error'&&g2p.error&&` · ${g2p.error}`}
    </p>}
    {(waiting||g2pWaiting||runtimeWaiting)&&<button disabled={starting||busy} onClick={()=>void download()}>{label}</button>}
    {runtime?.supported===false&&<p class="tts-lexicon-hint tts-lexicon-error">Kokoro cannot run on this platform: {runtime.error}. Use the OS voice engine instead.</p>}
  </div>
}

/**
 * The STT half of the first-use asset contract, and the deliberate sibling of
 * `KokoroModelPanel`: the same `not_downloaded → downloading → ready | error`
 * vocabulary, so an operator learns one shape for every model swe-mux fetches.
 *
 * Two things it does NOT do, both on purpose. It never starts a download on
 * mount — the whole defect this closes is that the first press of Talk fetched
 * gigabytes with no one asking — and it draws no percentage while downloading,
 * because `faster_whisper.download_model` disables the hub's progress hook and
 * there is nothing to read. Elapsed seconds is a reading; a bar would be fiction.
 */
export function WhisperModelPanel({initial,runtime}:{initial:WhisperModelInfo[]|null;runtime:VoiceRuntimeInfo|null}){
  const [models,setModels]=useState<WhisperModelInfo[]|null>(initial)
  const [busy,setBusy]=useState('')
  const refresh=()=>void api<{models:WhisperModelInfo[]}>('GET','/api/voice/models/whisper')
    .then(payload=>setModels(payload.models)).catch(()=>{})
  useEffect(()=>{setModels(initial);refresh()},[initial])
  useEffect(()=>{
    const handler=(raw:Event)=>{
      // The weights stores are labelled by model name rather than by store name,
      // because this panel tracks several at once and each row is one of them.
      const detail=(raw as CustomEvent).detail as ModelProgressEvent|undefined
      if(detail?.model&&models?.some(entry=>entry.model===detail.model))refresh()
      // The libraries are a step of this panel's own press, so its line has to
      // move while they are being acquired.
      if(assetFor(raw,'runtime'))refresh()
    }
    window.addEventListener('mux:voice-model',handler)
    return()=>window.removeEventListener('mux:voice-model',handler)
  },[models])
  useEffect(()=>{
    if(!models?.some(entry=>entry.status==='downloading'))return
    const timer=setInterval(refresh,2000)
    return()=>clearInterval(timer)
  },[models])
  const download=async(model:string)=>{
    setBusy(model)
    try{await api('POST','/api/voice/models/whisper/download',{model});refresh()}
    catch{/* surfaced by the next status refresh */}
    finally{setBusy('')}
  }
  if(!models?.length)return null
  // The libraries are a *step*, not a separate errand. Drawn without a button
  // because this panel's own Download starts them first and chains the weights
  // when they land (`routes/voice.whisper_model_download`), and because the
  // button that used to be hidden here - gated on `backend_installed`, which is
  // false precisely when the libraries are absent - left a fresh install reading
  // "faster-whisper not installed" with nothing to press.
  const blocked=Boolean(runtime)&&runtime?.supported===false
  return <div class="kokoro-model-panel">
    {runtime&&<VoiceRuntimePanel initial={runtime} action={false}/>}
    {models.map(entry=>{
      const size=entry.size_hint?` · ${entry.size_hint}`:''
      const acquiring=Boolean(runtime)&&runtime?.status==='downloading'
      return <p key={entry.model} aria-live="polite">
        <span class={`state-dot ${entry.status==='ready'?'idle':entry.status==='downloading'||acquiring?'running':'stopped'}`}/>
        Speech model {entry.model}::{entry.status}
        {entry.status==='downloading'&&` · downloading${size}${entry.elapsed_seconds?` · ${Math.round(entry.elapsed_seconds)}s elapsed`:''}`}
        {entry.status!=='downloading'&&acquiring&&' · waiting for the speech libraries'}
        {entry.status==='not_downloaded'&&!acquiring&&` · nothing has been downloaded${size}`}
        {entry.status==='error'&&entry.error&&` · ${entry.error}`}
        {!blocked&&!acquiring&&entry.status!=='ready'&&entry.status!=='downloading'&&
          <button disabled={busy===entry.model} onClick={()=>void download(entry.model)}>
            {entry.status==='error'?'Retry download':`Download ${entry.model}${entry.size_hint?` (${entry.size_hint})`:''}`}
          </button>}
      </p>
    })}
    {blocked&&<p class="tts-lexicon-hint tts-lexicon-error">On-device dictation cannot run on this platform: {runtime?.error}. Use Windows Speech Recognition instead.</p>}
  </div>
}

