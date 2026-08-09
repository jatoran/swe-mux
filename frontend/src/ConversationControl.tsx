import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { buildVoiceMatcher, conversationCapability, DEFAULT_COMMANDS, DEFAULT_WAKE_WORDS, PersistentVoiceCapture } from './conversation'
import { appendUtterance, clearDraft, editDraft, EMPTY_DRAFT, undoUtterance } from './conversationDraft'
import type { Draft } from './conversationDraft'
import { enableMobileVoice, mobileVoiceDestination } from './mobileVoice'
import type { Session, VoiceClip, VoiceStatus } from './types'
import { bargeInPlayback, getPlayback, playClip, unlockPlayback } from './voice'
import { reportPromptSubmitted } from './projectRecency'

type Phase='off'|'starting'|'listening'|'hearing'|'transcribing'|'sending'|'standby'|'error'

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
  const sessionRef=useRef<Session|null>(null)
  const captureRef=useRef<PersistentVoiceCapture|null>(null)
  const enabledRef=useRef(false)
  const standbyRef=useRef(false)
  const draftRef=useRef<Draft>(EMPTY_DRAFT)
  const queueRef=useRef<Promise<void>>(Promise.resolve())
  // Capture callbacks outlive the render that built them, so everything they read
  // from props goes through a ref: a wake word or command phrase edited in Settings
  // mid-dictation has to take effect without restarting the microphone.
  const statusRef=useRef(status);statusRef.current=status
  const matcherRef=useRef(matcher);matcherRef.current=matcher
  const wakeRef=useRef(wake);wakeRef.current=wake
  const onSessionRef=useRef(onSession);onSessionRef.current=onSession

  const setDraft=(next:Draft)=>{draftRef.current=next;setDraftState(next)}
  const enterStandby=(value:boolean)=>{standbyRef.current=value;setStandby(value)}

  const stop=()=>{
    enabledRef.current=false;enterStandby(false)
    captureRef.current?.stop();captureRef.current=null
    sessionRef.current=null
    setActive(false);setSessionId(null);setPhase('off');setDetail('')
  }
  const stopRef=useRef(stop);stopRef.current=stop
  // Capture is a device resource: a reload or a route away from the workspace must
  // release the microphone rather than leave the browser's recording indicator lit.
  useEffect(()=>()=>stopRef.current(),[])

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

  const handleTranscript=async(transcript:string)=>{
    if(!enabledRef.current)return
    const parsed=matcherRef.current.parse(transcript)
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

  const transcribe=async(audio:Blob)=>{
    const session=sessionRef.current
    if(!enabledRef.current||!session)return
    if(!standbyRef.current){setPhase('transcribing');setDetail('Transcribing locally on muxd…')}
    const response=await fetch(`/api/sessions/${encodeURIComponent(session.id)}/voice/transcribe`,{method:'POST',headers:{'Content-Type':'audio/wav'},body:audio})
    const payload=await response.json() as {text?:string;error?:string}
    if(!response.ok)throw new Error(payload.error||`transcription failed (${response.status})`)
    await handleTranscript(String(payload.text||''))
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
      onUtterance:audio=>{queueRef.current=queueRef.current.then(()=>transcribe(audio)).catch(cause=>{if(enabledRef.current)reportFailure(cause)})},
      onError:message=>{if(enabledRef.current){setPhase('error');setDetail(message)}},
    })
    try{
      await capture.start()
      // A second pane's chip clicked during the permission prompt already owns the
      // controller. Adopting this capture now would send its utterances to the pane
      // the user just left, so the losing start releases the microphone instead.
      if(sessionRef.current!==session){capture.stop();return}
      captureRef.current=capture;enabledRef.current=true;enterStandby(false);setActive(true)
      setPhase('listening');setDetail(`Listening. Say “${wake}, send” to submit.`)
    }catch(cause){capture.stop();if(sessionRef.current!==session)return;enabledRef.current=false;setActive(false);reportFailure(cause)}
  }

  return {
    sessionId,phase,detail,draft:draft.text,active,standby,wake,landedAt,
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
    :phase==='standby'?'talk:standby'
    :phase==='hearing'?'talk:hearing'
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
      <span class={`dictation-phase ${conversation.phase}`}>talk:{conversation.phase==='transcribing'?'typing':conversation.phase}</span>
      <span class="dictation-detail" role="status" aria-live="polite">{conversation.detail}</span>
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
