import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { buildVoiceMatcher, conversationCapability, DEFAULT_COMMANDS, DEFAULT_WAKE_WORDS, PersistentVoiceCapture } from './conversation'
import { enableMobileVoice, mobileVoiceDestination } from './mobileVoice'
import type { Session, VoiceClip, VoiceStatus } from './types'
import { bargeInPlayback, getPlayback, playClip, setAutoplayEnabled, unlockPlayback } from './voice'

type Phase='off'|'starting'|'listening'|'hearing'|'transcribing'|'sending'|'standby'|'error'

// The first configured wake word, shown in prompts so the hints match the user's
// own trigger word rather than a hardcoded "Mux".
const primaryWake=(words?:string[])=>(words&&words.find(word=>word.trim())||DEFAULT_WAKE_WORDS[0])

export function ConversationControl({session,status,onSession}:{session:Session;status:VoiceStatus;onSession:(session:Session)=>void}){
  const wake=primaryWake(status.wake_words)
  const matcher=useMemo(
    ()=>buildVoiceMatcher(status.wake_words?.length?status.wake_words:DEFAULT_WAKE_WORDS,status.commands?.length?status.commands:DEFAULT_COMMANDS),
    [status.wake_words,status.commands],
  )
  const [phase,setPhase]=useState<Phase>('off')
  const [buffer,setBuffer]=useState('')
  const [detail,setDetail]=useState(`Say “${wake}, send” when your message is ready.`)
  const captureRef=useRef<PersistentVoiceCapture|null>(null)
  const enabledRef=useRef(false)
  const standbyRef=useRef(false)
  const segmentsRef=useRef<string[]>([])
  const queueRef=useRef<Promise<void>>(Promise.resolve())

  const setSegments=(values:string[])=>{segmentsRef.current=values;setBuffer(values.join(' ').trim())}
  const appendSegment=(value:string)=>{
    const next=value.trim()?[...segmentsRef.current,value.trim()]:segmentsRef.current
    setSegments(next);return next.join(' ').trim()
  }
  const stop=()=>{
    enabledRef.current=false;standbyRef.current=false;captureRef.current?.stop();captureRef.current=null
    setPhase('off');setDetail(`Say “${wake}, send” when your message is ready.`)
  }

  useEffect(()=>{
    const claimed=(event:Event)=>{
      const owner=String((event as CustomEvent<{sessionId:string}>).detail?.sessionId||'')
      if(enabledRef.current&&owner!==session.id)stop()
    }
    window.addEventListener('mux:conversation-claim',claimed)
    return()=>{window.removeEventListener('mux:conversation-claim',claimed);stop()}
  },[session.id])

  const submit=async(text:string)=>{
    if(!text){setPhase('listening');setDetail(`Nothing buffered yet. Keep talking, then say “${wake}, send”.`);return}
    setPhase('sending');setDetail('Submitting voice message…')
    const utteranceId=globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random()}`
    await api('POST',`/api/sessions/${session.id}/voice/submit`,{utterance_id:utteranceId,text})
    setSegments([]);setPhase('listening');setDetail('Sent. Still listening for your next message.')
  }

  const handleTranscript=async(transcript:string)=>{
    if(!enabledRef.current)return
    const parsed=matcher.parse(transcript)
    if(standbyRef.current){
      // Standby keeps the mic and transcription running but ignores everything
      // except the resume/stop commands, so speech does nothing until woken.
      if(parsed.command==='resume'){
        standbyRef.current=false;setPhase('listening');setDetail('Resumed. Listening for your message.');return
      }
      if(parsed.command==='stop'){stop();return}
      setPhase('standby');setDetail(`Standby — say “${wake}, resume” to continue.`);return
    }
    if(parsed.command==='standby'){
      standbyRef.current=true;setPhase('standby')
      setDetail(`Standby. Still listening; say “${wake}, resume” to continue.`);return
    }
    if(parsed.command==='cancel'){
      setSegments([]);setPhase('listening');setDetail('Voice message cleared. Still listening.');return
    }
    if(parsed.command==='undo'){
      const next=segmentsRef.current.slice(0,-1);setSegments(next);setPhase('listening')
      setDetail(next.length?`Removed the last phrase. Buffered: ${next.join(' ')}`:'Removed the last phrase. Buffer is empty.');return
    }
    const pending=appendSegment(parsed.text)
    if(parsed.command==='send'){
      try{await submit(pending)}catch(cause){setPhase('error');setDetail(cause instanceof Error?cause.message:String(cause))}
      return
    }
    if(parsed.command==='mute'){
      bargeInPlayback();setPhase('listening');setDetail('Playback stopped. Still listening.');return
    }
    if(parsed.command==='read'){
      setPhase('sending');setDetail('Preparing the latest reply…')
      try{
        const clip=await api<VoiceClip>('POST',`/api/sessions/${session.id}/voice/generate`)
        unlockPlayback();await playClip(clip.id);setPhase('listening');setDetail('Reply read. Still listening.')
      }catch(cause){setPhase('error');setDetail(cause instanceof Error?cause.message:String(cause))}
      return
    }
    if(parsed.command==='summary'||parsed.command==='verbatim'){
      const mode=parsed.command
      try{
        const updated=await api<Session>('PATCH',`/api/sessions/${session.id}`,{voice_content:mode})
        onSession(updated);setPhase('listening');setDetail(`${mode==='summary'?'Summary':'Verbatim'} replies enabled. Still listening.`)
      }catch(cause){setPhase('error');setDetail(cause instanceof Error?cause.message:String(cause))}
      return
    }
    if(parsed.command==='help'){
      setPhase('listening');setDetail(`${wake} commands: send, cancel, undo, mute, read reply, summary, verbatim, interrupt, standby/resume, stop.`);return
    }
    if(parsed.command==='interrupt'){
      bargeInPlayback();setPhase('listening');setDetail('Playback stopped; interrupt sent to the agent.')
      try{await api('POST',`/api/sessions/${session.id}/voice/interrupt`)}catch(cause){setPhase('error');setDetail(cause instanceof Error?cause.message:String(cause))}
      return
    }
    if(parsed.command==='stop'){
      stop();return
    }
    if(parsed.text)setDetail(`Buffered: ${pending}`)
    setPhase('listening')
  }

  const transcribe=async(audio:Blob)=>{
    if(!enabledRef.current)return
    if(!standbyRef.current){setPhase('transcribing');setDetail('Transcribing locally on muxd…')}
    const response=await fetch(`/api/sessions/${encodeURIComponent(session.id)}/voice/transcribe`,{method:'POST',headers:{'Content-Type':'audio/wav'},body:audio})
    const payload=await response.json() as {text?:string;error?:string}
    if(!response.ok)throw new Error(payload.error||`transcription failed (${response.status})`)
    await handleTranscript(String(payload.text||''))
  }

  const start=async()=>{
    if(enabledRef.current){stop();return}
    if(!status.enabled){setPhase('error');setDetail('Enable read aloud in Settings → Voice first.');return}
    const capability=conversationCapability()
    if(!capability.secureContext){
      setPhase('starting');setDetail('Creating a private HTTPS address for mobile voice…')
      try{
        const setup=await enableMobileVoice()
        if(!setup.url){setDetail('Complete the one-time Tailscale approval, then tap Talk again.');return}
        setDetail(setup.status==='ready'?'Secure mobile voice verified. Opening it…':'Secure address created. Opening it…')
        location.assign(mobileVoiceDestination(setup.url))
      }catch(cause){setPhase('error');setDetail(cause instanceof Error?cause.message:String(cause))}
      return
    }
    if(!capability.available||!status.stt_available){setPhase('error');setDetail(capability.available?(status.stt_diagnostic||'Daemon transcription is unavailable.'):capability.reason);return}
    setPhase('starting');setDetail('Requesting microphone…')
    unlockPlayback();setAutoplayEnabled(true)
    window.dispatchEvent(new CustomEvent('mux:conversation-claim',{detail:{sessionId:session.id}}))
    const capture=new PersistentVoiceCapture({
      playbackActive:()=>getPlayback().playing,
      onSpeechStart:()=>{if(!enabledRef.current)return;if(standbyRef.current){setPhase('standby');return}bargeInPlayback();setPhase('hearing');setDetail('Listening…')},
      onUtterance:audio=>{queueRef.current=queueRef.current.then(()=>transcribe(audio)).catch(cause=>{if(enabledRef.current){setPhase('error');setDetail(cause instanceof Error?cause.message:String(cause))}})},
      onError:message=>{if(enabledRef.current){setPhase('error');setDetail(message)}},
    })
    try{
      await capture.start();captureRef.current=capture;enabledRef.current=true;standbyRef.current=false
      setPhase('listening');setDetail(`Listening. Say “${wake}, send” to submit.`)
      if(session.voice_mode!=='auto')void api<Session>('PATCH',`/api/sessions/${session.id}`,{voice_mode:'auto'}).then(onSession).catch(()=>{})
    }catch(cause){capture.stop();enabledRef.current=false;setPhase('error');setDetail(cause instanceof Error?cause.message:String(cause))}
  }

  const active=enabledRef.current
  const label=!active?'talk:off':phase==='error'?'talk:error':phase==='standby'?'talk:standby':phase==='hearing'?'talk:hearing':phase==='transcribing'?'talk:typing':'talk:on'
  const title=!active
    ?`Start hands-free conversation · ${detail}`
    :`Stop hands-free conversation · ${detail}${buffer?` · buffered: ${buffer}`:''}`
  return <>
    <button class={`conversation-chip ${phase}`} aria-pressed={enabledRef.current} aria-label={`Hands-free conversation for ${session.name}: ${phase}`} title={title} onClick={()=>void start()}>{label}</button>
    {phase!=='off'&&<span class={`conversation-status ${phase}`} role={phase==='error'?'alert':'status'} aria-live="polite" title={detail}>{phase==='error'?`error: ${detail}`:buffer?`heard:${buffer.slice(-72)}`:phase}</span>}
  </>
}
