import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { buildVoiceMatcher, conversationCapability, DEFAULT_COMMANDS, DEFAULT_WAKE_WORDS, PersistentVoiceCapture } from './conversation'
import type { CaptureDetector, ParsedMuxVoice } from './conversation'
import { appendUtterance, clearDraft, editDraft, EMPTY_DRAFT, undoUtterance } from './conversationDraft'
import type { Draft } from './conversationDraft'
import { enableMobileVoice, mobileVoiceDestination } from './mobileVoice'
import { buildLatencySample, formatLatency } from './voiceLatency'
import type { CaptureMarks, LatencySample, ServerTimings } from './voiceLatency'
import type { Session, VoiceClip, VoiceStatus } from './types'
import { bargeInPlayback, getPlayback, playClip, unlockPlayback } from './voice'
import { reportPromptSubmitted } from './projectRecency'

// `heard` exists to answer the user the instant the endpoint fires, before any text
// can exist. Silence after speaking reads as broken; the same silence after an
// acknowledgement reads as thinking.
//
// `warming` is never set directly: it is derived, and reported while the microphone is
// open but the speech detector has not finished loading. Capture is genuinely
// listening in that window — on the energy detector and its 900 ms tail — so calling
// it `listening` would be true and still misleading, and calling it `starting` would
// be a lie in the other direction.
type Phase='off'|'starting'|'warming'|'listening'|'hearing'|'heard'|'transcribing'|'sending'|'standby'|'error'

// The first configured wake word, shown in prompts so the hints match the user's
// own trigger word rather than a hardcoded "Mux".
const primaryWake=(words?:string[])=>(words&&words.find(word=>word.trim())||DEFAULT_WAKE_WORDS[0])

export type Conversation={
  /** The session that owns the microphone, or null when nothing is dictating. */
  sessionId:string|null
  phase:Phase
  detail:string
  draft:string
  /** True only while capture is live; a failed start keeps the panel up to report why. */
  active:boolean
  standby:boolean
  wake:string
  /** Bumped whenever an utterance lands, so the panel can flash what just arrived. */
  landedAt:number
  /** Stage breakdown for the most recent utterance; null until one completes. */
  latency:LatencySample|null
  /**
   * Which speech detector capture settled on, which sets how long a pause has to be.
   * Null while it is still loading — reported as the `warming` phase.
   */
  detector:CaptureDetector|null
  toggle:(session:Session)=>void
  stop:()=>void
  send:()=>void
  undo:()=>void
  clear:()=>void
  toggleStandby:()=>void
  edit:(text:string)=>void
}

/**
 * Hands-free capture, held once for the whole browser.
 *
 * Exactly one pane can own the microphone (`PersistentVoiceCapture` is a device
 * singleton), so this lives at the app root rather than once per pane: the chip in
 * each pane header and the dictation panel under the owning pane are two views of
 * the same controller. Keeping it here is also what removed the pane-to-pane
 * "conversation claim" event — there is only one instance left to claim from.
 */
export function useConversation(status:VoiceStatus|null,onSession:(session:Session)=>void):Conversation{
  const wake=primaryWake(status?.wake_words)
  const matcher=useMemo(
    ()=>buildVoiceMatcher(status?.wake_words?.length?status.wake_words:DEFAULT_WAKE_WORDS,status?.commands?.length?status.commands:DEFAULT_COMMANDS),
    [status?.wake_words,status?.commands],
  )
  const [sessionId,setSessionId]=useState<string|null>(null)
  const [phase,setPhase]=useState<Phase>('off')
  const [detail,setDetail]=useState('')
  const [draft,setDraftState]=useState<Draft>(EMPTY_DRAFT)
  const [active,setActive]=useState(false)
  const [standby,setStandby]=useState(false)
  const [landedAt,setLandedAt]=useState(0)
  const [latency,setLatency]=useState<LatencySample|null>(null)
  // null while the detector is still resolving, which is a different state from
  // having settled on the energy fallback: one is "not yet", the other is "Silero
  // failed and this is as good as it gets".
  const [detector,setDetector]=useState<CaptureDetector|null>(null)
  const latencyRef=useRef<LatencySample|null>(null)
  const sessionRef=useRef<Session|null>(null)
  const captureRef=useRef<PersistentVoiceCapture|null>(null)
  const enabledRef=useRef(false)
  const standbyRef=useRef(false)
  const draftRef=useRef<Draft>(EMPTY_DRAFT)
  const queueRef=useRef<Promise<void>>(Promise.resolve())
  // The speculative decode in flight, so speech resuming can abort it rather than
  // leave the daemon decoding audio whose result is already void.
  const speculationRef=useRef<{utteranceId:string;controller:AbortController}|null>(null)
  // Capture callbacks outlive the render that built them, so everything they read
  // from props goes through a ref: a wake word or command phrase edited in Settings
  // mid-dictation has to take effect without restarting the microphone.
  const statusRef=useRef(status);statusRef.current=status
  const matcherRef=useRef(matcher);matcherRef.current=matcher
  const wakeRef=useRef(wake);wakeRef.current=wake
  const onSessionRef=useRef(onSession);onSessionRef.current=onSession

  const listeningDetail=()=>`Listening. Say “${wakeRef.current}, send” to submit.`
  // Matched exactly when the detector resolves, so the readiness line is replaced only
  // if it is still the thing on screen. Comparing the text avoids tracking the phase
  // in a ref that a mid-utterance callback could read one render stale.
  const WARMING_DETAIL='Starting the speech detector — utterances need a longer pause until it loads.'

  const setDraft=(next:Draft)=>{draftRef.current=next;setDraftState(next)}
  const enterStandby=(value:boolean)=>{standbyRef.current=value;setStandby(value)}

  const stop=()=>{
    enabledRef.current=false;enterStandby(false)
    captureRef.current?.stop();captureRef.current=null
    sessionRef.current=null
    setActive(false);setSessionId(null);setPhase('off');setDetail('');setDetector(null)
  }
  const stopRef=useRef(stop);stopRef.current=stop
  // Capture is a device resource: a reload or a route away from the workspace must
  // release the microphone rather than leave the browser's recording indicator lit.
  useEffect(()=>()=>stopRef.current(),[])

  /**
   * Push-to-talk: hold the chord and there is no endpointing at all.
   *
   * The escape hatch for when detection is the problem rather than the fix — a noisy
   * room, a deliberate pause mid-sentence, a phrase the VAD keeps cutting. The key
   * release *is* the endpoint, so the whole trailing-silence question disappears.
   *
   * Captured on the window rather than routed through the command registry, because
   * a registry command fires on press and this needs both edges. Blur ends it too: a
   * key released over another window would otherwise leave the mic latched open.
   */
  useEffect(()=>{
    const engaged={current:false}
    const begin=()=>{
      if(engaged.current||!enabledRef.current||!captureRef.current)return
      engaged.current=true
      captureRef.current.beginPushToTalk()
      setPhase('hearing');setDetail('Push to talk — release to send the phrase.')
    }
    const end=()=>{
      if(!engaged.current)return
      engaged.current=false
      captureRef.current?.endPushToTalk()
    }
    const down=(event:KeyboardEvent)=>{
      if(event.code!=='Space'||!event.ctrlKey||!event.altKey||event.repeat)return
      if(!enabledRef.current||!captureRef.current)return
      event.preventDefault();event.stopPropagation()
      begin()
    }
    const up=(event:KeyboardEvent)=>{
      // Any part of the chord releasing ends the phrase; the modifiers and the key
      // are rarely released in the same order twice.
      if(event.code==='Space'||event.key==='Control'||event.key==='Alt')end()
    }
    window.addEventListener('keydown',down,true)
    window.addEventListener('keyup',up,true)
    window.addEventListener('blur',end)
    return()=>{
      end()
      window.removeEventListener('keydown',down,true)
      window.removeEventListener('keyup',up,true)
      window.removeEventListener('blur',end)
    }
  },[])

  const submit=async(text:string)=>{
    const session=sessionRef.current
    if(!session)return
    const body=text.trim()
    if(!body){setPhase('listening');setDetail(`Nothing buffered yet. Keep talking, then say “${wakeRef.current}, send”.`);return}
    setPhase('sending');setDetail('Submitting voice message…')
    const utteranceId=globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random()}`
    await api('POST',`/api/sessions/${session.id}/voice/submit`,{utterance_id:utteranceId,text:body})
    reportPromptSubmitted(session.id)
    setDraft(clearDraft());setPhase('listening');setDetail('Sent. Still listening for your next message.')
  }
  const reportFailure=(cause:unknown)=>{setPhase('error');setDetail(cause instanceof Error?cause.message:String(cause))}

  const handleTranscript=async(parsed:ParsedMuxVoice)=>{
    if(!enabledRef.current)return
    const wakeWord=wakeRef.current
    if(standbyRef.current){
      // Standby keeps the mic and transcription running but ignores everything
      // except the resume/stop commands, so speech does nothing until woken.
      if(parsed.command==='resume'){
        enterStandby(false);setPhase('listening');setDetail('Resumed. Listening for your message.');return
      }
      if(parsed.command==='stop'){stop();return}
      setPhase('standby');setDetail(`Standby — say “${wakeWord}, resume” to continue.`);return
    }
    if(parsed.command==='standby'){
      enterStandby(true);setPhase('standby')
      setDetail(`Standby. Still listening; say “${wakeWord}, resume” to continue.`);return
    }
    if(parsed.command==='cancel'){
      setDraft(clearDraft());setPhase('listening');setDetail('Voice message cleared. Still listening.');return
    }
    if(parsed.command==='undo'){
      const next=undoUtterance(draftRef.current);setDraft(next);setPhase('listening')
      setDetail(next.text?'Removed the last phrase.':'Removed the last phrase. Draft is empty.');return
    }
    const pending=appendUtterance(draftRef.current,parsed.text)
    if(pending!==draftRef.current){setDraft(pending);setLandedAt(current=>current+1)}
    if(parsed.command==='send'){
      try{await submit(pending.text)}catch(cause){reportFailure(cause)}
      return
    }
    if(parsed.command==='mute'){
      bargeInPlayback();setPhase('listening');setDetail('Playback stopped. Still listening.');return
    }
    if(parsed.command==='read'){
      const session=sessionRef.current
      if(!session)return
      if(!statusRef.current?.enabled){setPhase('listening');setDetail('Read aloud is off — enable it in Settings → Voice. Still listening.');return}
      setPhase('sending');setDetail('Preparing the latest reply…')
      try{
        const clip=await api<VoiceClip>('POST',`/api/sessions/${session.id}/voice/generate`)
        unlockPlayback();await playClip(clip.id,session.id);setPhase('listening');setDetail('Reply read. Still listening.')
      }catch(cause){reportFailure(cause)}
      return
    }
    if(parsed.command==='summary'||parsed.command==='verbatim'){
      const session=sessionRef.current
      if(!session)return
      const mode=parsed.command
      try{
        const updated=await api<Session>('PATCH',`/api/sessions/${session.id}`,{voice_content:mode})
        onSessionRef.current(updated);setPhase('listening');setDetail(`${mode==='summary'?'Summary':'Verbatim'} replies enabled. Still listening.`)
      }catch(cause){reportFailure(cause)}
      return
    }
    if(parsed.command==='help'){
      setPhase('listening');setDetail(`${wakeWord} commands: send, cancel, undo, mute, read reply, summary, verbatim, interrupt, standby/resume, stop.`);return
    }
    if(parsed.command==='interrupt'){
      const session=sessionRef.current
      if(!session)return
      bargeInPlayback();setPhase('listening');setDetail('Playback stopped; interrupt sent to the agent.')
      try{await api('POST',`/api/sessions/${session.id}/voice/interrupt`)}catch(cause){reportFailure(cause)}
      return
    }
    if(parsed.command==='stop'){
      stop();return
    }
    setPhase('listening');setDetail(`Listening. Say “${wakeWord}, send” to submit.`)
  }

  // Latency reporting runs after the action has already happened, so it can never
  // add to the number it measures; a failed report is dropped rather than surfaced,
  // because a diagnostic that can fail an utterance is worse than no diagnostic.
  const reportLatency=(sample:LatencySample)=>{
    latencyRef.current=sample;setLatency(sample)
    void api('POST','/api/voice/stt-latency',sample,{timeoutMs:4000}).catch(()=>{})
  }

  type Decoded={parsed:ParsedMuxVoice;postAt:number;responseAt:number;server:ServerTimings}
  const decode=async(audio:Blob,marks:CaptureMarks,profile:'command'|'dictation',signal?:AbortSignal):Promise<Decoded>=>{
    const session=sessionRef.current
    if(!session)throw new Error('no session owns the microphone')
    const postAt=performance.now()
    const response=await fetch(`/api/sessions/${encodeURIComponent(session.id)}/voice/transcribe`,{
      method:'POST',
      // The profile picks the daemon's decoder: a routing pass wants the fast small
      // English model and its own slot, so a speculative decode cannot delay the
      // real one behind a shared lock.
      headers:{'Content-Type':'audio/wav','X-Mux-Utterance-Id':marks.utteranceId,'X-Mux-Decode-Profile':profile},
      body:audio,
      signal,
    })
    const payload=await response.json() as {text?:string;error?:string;timings?:ServerTimings}
    const responseAt=performance.now()
    if(!response.ok)throw new Error(payload.error||`transcription failed (${response.status})`)
    return {parsed:matcherRef.current.parse(String(payload.text||'')),postAt,responseAt,server:payload.timings||{}}
  }

  const transcribe=async(audio:Blob,marks:CaptureMarks)=>{
    if(!enabledRef.current||!sessionRef.current)return
    if(!standbyRef.current){setPhase('transcribing');setDetail('Transcribing locally on muxd…')}
    // Whisper models are cached for the life of the *daemon*, not the tab, so the
    // first utterance after a restart or a redeploy waits seconds on a model load.
    // Naming it is the whole fix: an unexplained pause here reads as a hang.
    const slow=setTimeout(()=>{
      if(enabledRef.current&&!standbyRef.current)setDetail('Loading the transcription model — the first utterance after a daemon restart is slow.')
    },1_200)
    let decoded:Decoded
    try{decoded=await decode(audio,marks,'dictation')}
    finally{clearTimeout(slow)}
    try{await handleTranscript(decoded.parsed)}
    finally{
      reportLatency(buildLatencySample({
        marks,postAt:decoded.postAt,responseAt:decoded.responseAt,actionAt:performance.now(),
        server:decoded.server,command:decoded.parsed.command||'',
      }))
    }
  }

  /**
   * A decode started before the utterance is certainly over.
   *
   * Only a complete wake word plus command phrase acts on it, per the suffix
   * grammar: that is the one shape whose presence at the end of a transcript is
   * strong evidence the speaker has finished, so commands skip the rest of the
   * trailing silence while dictation still waits it out. Everything else is
   * discarded — the same audio arrives again as the real utterance moments later.
   *
   * `commitSpeculative` is the race guard: it refuses if speech resumed between the
   * decode starting and the text arriving, which is what stops "…mux, send me the
   * file" from submitting at the pause after "send".
   */
  const speculate=async(audio:Blob,marks:CaptureMarks)=>{
    const capture=captureRef.current
    if(!enabledRef.current||!capture||standbyRef.current)return
    const controller=new AbortController()
    speculationRef.current={utteranceId:marks.utteranceId,controller}
    let decoded:Decoded
    try{decoded=await decode(audio,marks,'command',controller.signal)}
    // A speculative decode is invisible by design: it may be aborted, refused while
    // the daemon is busy, or return nothing, and none of that is the user's problem.
    catch{return}
    finally{if(speculationRef.current?.utteranceId===marks.utteranceId)speculationRef.current=null}
    if(!decoded.parsed.command)return
    if(!capture.commitSpeculative(marks.utteranceId))return
    try{await handleTranscript(decoded.parsed)}
    finally{
      reportLatency(buildLatencySample({
        marks:{...marks,speculative:true},postAt:decoded.postAt,responseAt:decoded.responseAt,
        actionAt:performance.now(),server:decoded.server,command:decoded.parsed.command||'',
      }))
    }
  }

  // Talk mode is mic → transcribe → PTY. It does not require read aloud and never
  // changes it: the tts chip, the device autoplay toggle, and this button are three
  // independent switches. Only the read/summary/verbatim voice commands need TTS,
  // and each reports that itself.
  const start=async(session:Session)=>{
    if(enabledRef.current)stop()
    // Claimed before the capability checks so a refusal has somewhere to be read:
    // the panel is the only surface that shows `detail` in full.
    sessionRef.current=session;setSessionId(session.id)
    const capability=conversationCapability()
    if(!capability.secureContext){
      setPhase('starting');setDetail('Creating a private HTTPS address for mobile voice…')
      try{
        const setup=await enableMobileVoice()
        if(!setup.url){setDetail('Complete the one-time Tailscale approval, then tap Talk again.');return}
        setDetail(setup.status==='ready'?'Secure mobile voice verified. Opening it…':'Secure address created. Opening it…')
        location.assign(mobileVoiceDestination(setup.url))
      }catch(cause){reportFailure(cause)}
      return
    }
    if(!capability.available||!statusRef.current?.stt_available){
      setPhase('error');setDetail(capability.available?(statusRef.current?.stt_diagnostic||'Daemon transcription is unavailable.'):capability.reason);return
    }
    setPhase('starting');setDetail('Requesting microphone…')
    // Unlock only: this is the gesture mobile browsers require before any later
    // programmatic play(), so the `read reply` command works. It turns nothing on.
    unlockPlayback()
    const capture=new PersistentVoiceCapture({
      playbackActive:()=>getPlayback().playing,
      onSpeechStart:()=>{if(!enabledRef.current)return;if(standbyRef.current){setPhase('standby');return}bargeInPlayback();setPhase('hearing');setDetail('Listening…')},
      // Fired at the endpoint, before any text exists. It is the whole point of the
      // phase: the answer has to arrive when the user stops talking, not when the
      // decode does, or the pause reads as a failure.
      onSpeechEnd:()=>{if(!enabledRef.current||standbyRef.current)return;setPhase('heard');setDetail('Heard you.')},
      onSpeculative:(audio,marks)=>{void speculate(audio,marks)},
      onSpeculativeAbort:utteranceId=>{
        const pending=speculationRef.current
        if(pending&&pending.utteranceId===utteranceId){pending.controller.abort();speculationRef.current=null}
      },
      // Utterances stay serialized so two of them can never interleave into the
      // draft out of order. Speculative decodes deliberately bypass this queue:
      // waiting behind the previous utterance would put them past their own endpoint.
      onUtterance:(audio,marks)=>{queueRef.current=queueRef.current.then(()=>transcribe(audio,marks)).catch(cause=>{if(enabledRef.current)reportFailure(cause)})},
      onDetector:(resolved,diagnostic)=>{
        setDetector(resolved)
        // Only the readiness line is replaced, and only if it is still what is on
        // screen: the detector can resolve in the middle of an utterance, and
        // overwriting "Heard you." with a status message would undo the one thing
        // that stage exists to say.
        if(enabledRef.current)setDetail(current=>current!==WARMING_DETAIL?current
          :resolved==='silero'?listeningDetail():diagnostic)
      },
      onError:message=>{if(enabledRef.current){setPhase('error');setDetail(message)}},
    })
    try{
      await capture.start()
      // A second pane's chip clicked during the permission prompt already owns the
      // controller. Adopting this capture now would send its utterances to the pane
      // the user just left, so the losing start releases the microphone instead.
      if(sessionRef.current!==session){capture.stop();return}
      captureRef.current=capture;enabledRef.current=true;enterStandby(false);setActive(true)
      // Always warming here: `start` fires the detector load without awaiting it, and
      // nothing between that and this line yields, so `onDetector` cannot have run
      // yet. The displayed phase derives `warming` from `detector` being null, so
      // readiness has one source of truth rather than a phase and a flag to disagree.
      setPhase('listening');setDetail(WARMING_DETAIL)
    }catch(cause){capture.stop();if(sessionRef.current!==session)return;enabledRef.current=false;setActive(false);reportFailure(cause)}
  }

  return {
    sessionId,
    // Derived rather than stored: readiness has one source of truth (`detector`), and
    // an idle phase that claimed `listening` before the detector loaded would be true
    // and still misleading — capture is listening, on the fallback's 900 ms tail.
    phase:phase==='listening'&&detector===null?'warming':phase,
    detail,draft:draft.text,active,standby,wake,landedAt,latency,detector,
    toggle:session=>{
      if(sessionRef.current?.id===session.id&&(enabledRef.current||phase==='error')){stop();return}
      void start(session)
    },
    stop,
    send:()=>{void (async()=>{try{await submit(draftRef.current.text)}catch(cause){reportFailure(cause)}})()},
    undo:()=>{
      const next=undoUtterance(draftRef.current);setDraft(next)
      setDetail(next.text?'Removed the last phrase.':'Removed the last phrase. Draft is empty.')
    },
    clear:()=>{setDraft(clearDraft());setDetail('Draft cleared. Still listening.')},
    toggleStandby:()=>{
      const next=!standbyRef.current
      enterStandby(next)
      if(enabledRef.current)setPhase(next?'standby':'listening')
      setDetail(next?`Standby. Still listening; say “${wakeRef.current}, resume” to continue.`:'Resumed. Listening for your message.')
    },
    edit:text=>{setDraft(editDraft(text))},
  }
}

/**
 * The pane header's Talk switch. It carries the state at a glance and nothing else —
 * the live transcript used to sit beside it and could not fit, because `.pane-voice`
 * is a fixed-chip scroller in a bar that must never wrap (see `ui.md`). The draft
 * text belongs to the dictation panel one row below.
 */
export function ConversationChip({session,conversation}:{session:Session;conversation:Conversation}){
  const owned=conversation.sessionId===session.id
  const phase=owned?conversation.phase:'off'
  const listening=owned&&conversation.active
  const label=phase==='error'?'talk:error'
    :!listening?'talk:off'
    :phase==='warming'?'talk:warming'
    :phase==='standby'?'talk:standby'
    :phase==='hearing'?'talk:hearing'
    :phase==='heard'?'talk:heard'
    :phase==='transcribing'?'talk:typing'
    :'talk:on'
  const detail=owned&&conversation.detail?conversation.detail:`Say “${conversation.wake}, send” when your message is ready.`
  return <button
    class={`conversation-chip ${phase}`}
    aria-pressed={listening}
    aria-label={`Hands-free conversation for ${session.name}: ${phase}`}
    title={`${listening?'Stop':'Start'} hands-free conversation · ${detail}`}
    onClick={()=>conversation.toggle(session)}
  >{label}</button>
}

/**
 * The dictation draft, as its own pane row under the read-aloud strip.
 *
 * Fixed height on purpose: the terminal host is the pane's `1fr` row, so a panel
 * that grew with the draft would resize the PTY on every phrase and reflow the
 * agent's TUI. It costs the terminal the same rows for the whole time Talk is on,
 * one resize each way, exactly like the read-aloud strip above it.
 */
export function DictationPanel({conversation,onOpenSettings}:{conversation:Conversation;onOpenSettings:()=>void}){
  const draftRef=useRef<HTMLTextAreaElement|null>(null)
  const [landed,setLanded]=useState(false)
  // Whisper returns whole utterances, never partial words, so there is no stream to
  // animate. A brief flash is the honest signal that something arrived.
  useEffect(()=>{
    if(!conversation.landedAt)return
    setLanded(true)
    const timer=setTimeout(()=>setLanded(false),700)
    return()=>clearTimeout(timer)
  },[conversation.landedAt])
  // The textarea is never read-only and has no edit mode: it is the draft. Typing
  // while an utterance lands must not throw the caret to the end, so the value is
  // written imperatively and the selection restored around it (utterances always
  // append, so an offset from before the append stays meaningful).
  useLayoutEffect(()=>{
    const element=draftRef.current
    if(!element)return
    if(element.value!==conversation.draft){
      const focused=document.activeElement===element
      const caretAtEnd=element.selectionStart>=element.value.length
      const start=element.selectionStart
      const end=element.selectionEnd
      element.value=conversation.draft
      if(focused){
        const limit=element.value.length
        if(caretAtEnd)element.setSelectionRange(limit,limit)
        else element.setSelectionRange(Math.min(start,limit),Math.min(end,limit))
      // Only chase the tail when the user is not in the text: while they are editing, the
      // browser already keeps the caret in view, and forcing the bottom would yank them
      // away from a correction they are making in the middle of a long draft.
      }else element.scrollTop=element.scrollHeight
    }
    // Grow to fit, bounded by the min/max in CSS. Affordable only because the panel floats
    // — measuring requires collapsing to `auto` first, and `scrollHeight` excludes the
    // border that `box-sizing:border-box` makes the inline height responsible for.
    element.style.height='auto'
    element.style.height=`${element.scrollHeight+element.offsetHeight-element.clientHeight}px`
  },[conversation.draft])
  const send=()=>conversation.send()
  return <section class={`dictation-panel ${conversation.phase}${landed?' landed':''}`} aria-label="Voice dictation draft">
    <header>
      <span
        class={`dictation-phase ${conversation.phase}`}
        title={conversation.detector===null
          ? 'The speech detector is still loading. Capture is already listening, but on the fallback detector, so an utterance needs a longer pause to end. Hold Ctrl+Alt+Space to talk with no pause detection at all.'
          : conversation.detector==='silero'
            ? 'Silero voice activity detection: an utterance ends after a short pause. Hold Ctrl+Alt+Space to talk with no pause detection at all.'
            : 'Energy-based detection: an utterance needs a longer pause to end. Hold Ctrl+Alt+Space to talk with no pause detection at all.'}
      >talk:{conversation.phase==='transcribing'?'typing':conversation.phase}</span>
      <span class="dictation-detail" role="status" aria-live="polite">{conversation.detail}</span>
      {conversation.latency&&<span class="dictation-latency" title={`End of speech to action — ${formatLatency(conversation.latency)}`}>{Math.round(conversation.latency.total_ms)} ms</span>}
      <div class="dictation-actions">
        <button class="dictation-send" title="Send the draft to the agent (Ctrl+Enter)" disabled={!conversation.draft.trim()} onClick={send}>send</button>
        <button title="Remove the last phrase that was heard" disabled={!conversation.draft} onClick={()=>conversation.undo()}>undo</button>
        <button title="Clear the draft" disabled={!conversation.draft} onClick={()=>conversation.clear()}>clear</button>
        <button class={conversation.standby?'active':''} title={conversation.standby?'Resume listening':'Keep the mic open but ignore speech until resumed'} onClick={()=>conversation.toggleStandby()}>{conversation.standby?'resume':'standby'}</button>
        <button class="dictation-stop" title="Stop dictating and release the microphone" onClick={()=>conversation.stop()}>stop</button>
        <button class="dictation-settings" aria-label="Open Voice settings" title="Open Voice settings (engine, wake words, commands)" onClick={onOpenSettings}>⚙</button>
      </div>
    </header>
    <textarea
      ref={draftRef}
      class="dictation-draft"
      spellcheck={false}
      aria-label="Voice dictation draft — editable"
      placeholder={`Speak, then say “${conversation.wake}, send” — or type here to fix a word.`}
      onInput={event=>conversation.edit(event.currentTarget.value)}
      onKeyDown={event=>{
        if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();send();return}
        // Escape hands the keyboard back to the terminal rather than bubbling into
        // the app's global shortcuts, which would close an unrelated overlay.
        if(event.key==='Escape'){event.preventDefault();event.stopPropagation();draftRef.current?.blur()}
      }}
    />
  </section>
}
