import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { requestSetting } from './settingTargets'
import { buildVoiceMatcher, conversationCapability, DEFAULT_COMMANDS, DEFAULT_WAKE_WORDS, isPlaybackControl, PersistentVoiceCapture } from './conversation'
import type { CaptureDetector, ParsedMuxVoice } from './conversation'
import { appendUtterance, clearDraft, editDraft, EMPTY_DRAFT, undoUtterance } from './conversationDraft'
import type { Draft } from './conversationDraft'
import { enableMobileVoice, mobileVoiceDestination } from './mobileVoice'
import { buildLatencySample, formatLatency } from './voiceLatency'
import type { CaptureMarks, LatencySample, ServerTimings } from './voiceLatency'
import type { Session, VoiceClip, VoiceStatus } from './types'
import {
  autoplayEnabled, bargeInPlayback, beginRequestedStream, cancelRequestedStream, getPlayback,
  newVoiceStreamId, playRequestedStreamFirst, setAutoplayEnabled, setPlaybackDucked, unlockPlayback,
} from './voice'
import { reportPromptSubmitted } from './projectRecency'
import { conversationTargetAvailable, effectiveConversationTarget, toggleConversationTargetPin } from './conversationTarget'
import type { ConversationTarget } from './conversationTarget'
import { extractWakeIntent } from './voiceIntents'
import type { Command, VoiceCommandResult } from './commands'
import { safeDuringSystemPlayback } from './voiceQueries'
import {
  appendVoiceConversationEntry, loadVoiceConversationHistory, loadVoiceConversationHistoryOpen,
  saveVoiceConversationHistory, saveVoiceConversationHistoryOpen,
} from './voiceConversationHistory'
import type { VoiceConversationEntry, VoiceConversationRole } from './voiceConversationHistory'
import { insertIntoTerminal } from './terminalActions'
import { voiceCommsMessage } from './voiceComms'
import { VoiceCommandsButton } from './VoiceCommandsButton'
import { assistantFollowUpActive } from './assistant'
import { playEarcon } from './earcons'
import type { ComponentChildren } from 'preact'

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
  /** Current commit destination. Capture stays live when this changes. */
  target:ConversationTarget|null
  targetAvailable:boolean
  pinned:boolean
  phase:Phase
  detail:string
  draft:string
  /** True only while capture is live; a failed start keeps the panel up to report why. */
  active:boolean
  standby:boolean
  comms:boolean
  wake:string
  /** Bumped whenever an utterance lands, so the panel can flash what just arrived. */
  landedAt:number
  /** Stage breakdown for the most recent utterance; null until one completes. */
  latency:LatencySample|null
  /** Device-local app-wide history across focus and target changes. */
  history:VoiceConversationEntry[]
  /**
   * Which speech detector capture settled on, which sets how long a pause has to be.
   * Null while it is still loading — reported as the `warming` phase.
   */
  detector:CaptureDetector|null
  toggle:()=>void
  togglePin:()=>void
  stop:()=>void
  send:()=>void
  append:()=>void
  undo:()=>void
  clear:()=>void
  clearHistory:()=>void
  toggleStandby:()=>void
  toggleComms:()=>void
  edit:(text:string)=>void
}

/**
 * Hands-free capture, held once for the whole browser.
 *
 * `PersistentVoiceCapture` is a device singleton, so capture belongs to the workspace,
 * not to a pane. Focus supplies a replaceable commit target; pinning freezes that target
 * while the microphone and draft continue independently.
 */
export function useConversation(
  status:VoiceStatus|null,
  onSession:(session:Session)=>void,
  followingTarget:ConversationTarget|null,
  onIntent?:(spoken:string)=>VoiceCommandResult|Promise<VoiceCommandResult>,
  onAssistantUtterance?:(spoken:string)=>Promise<string|false>,
  assistantChatActive?:()=>boolean,
):Conversation{
  const wake=primaryWake(status?.wake_words)
  const matcher=useMemo(
    ()=>buildVoiceMatcher(status?.wake_words?.length?status.wake_words:DEFAULT_WAKE_WORDS,status?.commands?.length?status.commands:DEFAULT_COMMANDS),
    [status?.wake_words,status?.commands],
  )
  const [phase,setPhase]=useState<Phase>('off')
  const [detail,setDetail]=useState('')
  const [draft,setDraftState]=useState<Draft>(EMPTY_DRAFT)
  const [active,setActive]=useState(false)
  const [standby,setStandby]=useState(false)
  const [commsTargetId,setCommsTargetId]=useState<string|null>(null)
  const [landedAt,setLandedAt]=useState(0)
  const [latency,setLatency]=useState<LatencySample|null>(null)
  const [history,setHistory]=useState<VoiceConversationEntry[]>(()=>loadVoiceConversationHistory())
  // null while the detector is still resolving, which is a different state from
  // having settled on the energy fallback: one is "not yet", the other is "Silero
  // failed and this is as good as it gets".
  const [detector,setDetector]=useState<CaptureDetector|null>(null)
  const [pinnedTarget,setPinnedTarget]=useState<ConversationTarget|null>(null)
  const pinnedTargetRef=useRef<ConversationTarget|null>(null);pinnedTargetRef.current=pinnedTarget
  const target=effectiveConversationTarget(followingTarget,pinnedTarget)
  const targetAvailable=conversationTargetAvailable(target)
  const latencyRef=useRef<LatencySample|null>(null)
  const targetRef=useRef<ConversationTarget|null>(target);targetRef.current=target
  const captureRef=useRef<PersistentVoiceCapture|null>(null)
  const startingRef=useRef<PersistentVoiceCapture|null>(null)
  const enabledRef=useRef(false)
  const standbyRef=useRef(false)
  const draftRef=useRef<Draft>(EMPTY_DRAFT)
  const queueRef=useRef<Promise<void>>(Promise.resolve())
  const commsTargetRef=useRef<string|null>(null);commsTargetRef.current=commsTargetId
  const commsProtocolRuns=useRef<Set<string>>(new Set())
  const commsPrevious=useRef<Map<string,{voice_mode:Session['voice_mode'];voice_content:Session['voice_content']}>>(new Map())
  const commsPreviousAutoplay=useRef<boolean|null>(null)
  const commsPreviousPin=useRef<ConversationTarget|null>(null)
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
  const onIntentRef=useRef(onIntent);onIntentRef.current=onIntent
  const onAssistantRef=useRef(onAssistantUtterance);onAssistantRef.current=onAssistantUtterance
  const assistantChatRef=useRef(assistantChatActive);assistantChatRef.current=assistantChatActive

  const listeningDetail=()=>`Listening. Say “${wakeRef.current}, send” to submit.`
  // Matched exactly when the detector resolves, so the readiness line is replaced only
  // if it is still the thing on screen. Comparing the text avoids tracking the phase
  // in a ref that a mid-utterance callback could read one render stale.
  const WARMING_DETAIL='Starting the speech detector — utterances need a longer pause until it loads.'

  const setDraft=(next:Draft)=>{draftRef.current=next;setDraftState(next)}
  const enterStandby=(value:boolean)=>{standbyRef.current=value;setStandby(value)}
  const recordHistory=(role:VoiceConversationRole,text:string)=>setHistory(current=>{
    const next=appendVoiceConversationEntry(current,role,text)
    if(next!==current)saveVoiceConversationHistory(next)
    return next
  })
  const respond=(text:string)=>{setDetail(text);recordHistory('mux',text)}

  const stop=(record=true)=>{
    bargeInPlayback()
    if(record&&enabledRef.current)recordHistory('mux','Talk stopped.')
    enabledRef.current=false;enterStandby(false)
    speculationRef.current?.controller.abort();speculationRef.current=null
    startingRef.current?.stop();startingRef.current=null
    captureRef.current?.stop();captureRef.current=null
    setActive(false);setPhase('off');setDetail('');setDetector(null)
  }
  const stopRef=useRef(stop);stopRef.current=stop
  // Capture is a device resource: a reload or a route away from the workspace must
  // release the microphone rather than leave the browser's recording indicator lit.
  useEffect(()=>()=>stopRef.current(false),[])

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

  const commitToTarget=async(text:string,submit:boolean)=>{
    const body=text.trim()
    if(!body){setPhase('listening');respond(`Nothing buffered yet. Keep talking, then say “${wakeRef.current}, ${submit?'send':'append'}”.`);return}
    const target=targetRef.current
    if(!target||!conversationTargetAvailable(target)){
      setPhase('listening');respond('Draft kept. Focus an agent, note, Scratchpad, or Queue composer first.');return
    }
    setPhase('sending');setDetail(submit?'Submitting voice message…':'Appending voice message…')
    if(target.kind==='text'){
      target.editor.insertText(body)
      setDraft(clearDraft());setPhase('listening');respond(`Appended to ${target.label}. Still listening.`)
      return
    }
    const runKey=`${target.id}:${target.agentRunId()||'current'}`
    const comms=commsTargetRef.current===target.id
    const includeProtocol=comms&&!commsProtocolRuns.current.has(runKey)
    const terminalText=comms?voiceCommsMessage(body,includeProtocol):body
    await api('POST',`/api/sessions/${target.id}/voice/prepare-submit`,{text:terminalText})
    await insertIntoTerminal(target.id,terminalText,submit)
    if(includeProtocol)commsProtocolRuns.current.add(runKey)
    if(submit)reportPromptSubmitted(target.id)
    setDraft(clearDraft());setPhase('listening')
    respond(submit?'Sent through the agent composer. Still listening for your next message.':'Appended to the agent composer without sending. Still listening.')
  }
  const submit=(text:string)=>commitToTarget(text,true)
  const append=(text:string)=>commitToTarget(text,false)
  const reportFailure=(cause:unknown)=>{setPhase('error');respond(cause instanceof Error?cause.message:String(cause))}
  const sessionTarget=():Extract<ConversationTarget,{kind:'session'}>|null=>{
    const target=targetRef.current
    return target?.kind==='session'&&conversationTargetAvailable(target)?target:null
  }

  const toggleComms=async(force?:boolean)=>{
    const activeId=commsTargetRef.current
    const enable=force??!activeId
    if(enable&&activeId){setPhase('listening');respond('Voice comms is already on.');return}
    if(!enable){
      if(!activeId){setPhase('listening');respond('Voice comms is already off.');return}
      const previous=commsPrevious.current.get(activeId)
      commsTargetRef.current=null;setCommsTargetId(null)
      let restoreFailure:unknown=null
      if(previous){
        try{
          const updated=await api<Session>('PATCH',`/api/sessions/${activeId}`,previous)
          onSessionRef.current(updated)
          commsPrevious.current.delete(activeId)
        }catch(cause){restoreFailure=cause}
      }
      if(commsPreviousAutoplay.current!==null)setAutoplayEnabled(commsPreviousAutoplay.current)
      commsPreviousAutoplay.current=null
      setPinnedTarget(commsPreviousPin.current);commsPreviousPin.current=null
      if(restoreFailure){reportFailure(restoreFailure);return}
      setPhase('listening');respond('Voice comms off. Normal agent replies restored.');return
    }
    const target=sessionTarget()
    if(!target){setPhase('listening');respond('Voice comms needs a focused agent session.');return}
    if(!statusRef.current?.enabled){setPhase('listening');respond('Voice comms needs Read aloud enabled in Settings, Voice.');return}
    commsPrevious.current.set(target.id,{voice_mode:target.voiceMode(),voice_content:target.voiceContent()})
    try{
      const updated=await api<Session>('PATCH',`/api/sessions/${target.id}`,{voice_mode:'auto',voice_content:'verbatim'})
      onSessionRef.current(updated)
    }catch(cause){reportFailure(cause);return}
    commsPreviousAutoplay.current=autoplayEnabled()
    setAutoplayEnabled(true)
    commsPreviousPin.current=pinnedTargetRef.current
    commsTargetRef.current=target.id;setCommsTargetId(target.id);setPinnedTarget(target)
    setPhase('listening');respond(`Voice comms on for ${target.label}. Replies will be short and read aloud.`)
  }

  const speakSystem=async(text:string)=>{
    if(!statusRef.current?.enabled)throw new Error('Read aloud is off. Enable it in Settings, Voice.')
    const streamId=newVoiceStreamId()
    unlockPlayback();beginRequestedStream(streamId,'system','system')
    try{
      const clip=await api<VoiceClip>('POST','/api/voice/speak',{text,stream_id:streamId})
      await playRequestedStreamFirst(clip.id,clip.stream_id||streamId,'system','system')
    }catch(cause){cancelRequestedStream(streamId);throw cause}
  }

  const runRegistryIntent=async(spoken:string)=>{
    const handler=onIntentRef.current
    if(!handler)return false
    const outcome=await handler(spoken)
    setPhase('listening');setDetail(outcome.detail)
    recordHistory('mux',outcome.transcript||outcome.detail)
    if(outcome.speech){
      try{await speakSystem(outcome.speech)}
      catch(cause){
        const failure=`${outcome.detail} Read aloud failed: ${cause instanceof Error?cause.message:String(cause)}`
        setDetail(failure);recordHistory('mux',failure)
      }
    }
    return true
  }

  const handleTranscript=async(parsed:ParsedMuxVoice,rawText='')=>{
    if(!enabledRef.current)return
    const wakeWord=wakeRef.current
    if(standbyRef.current){
      // Standby keeps the mic and transcription running but ignores everything
      // except the resume/stop commands, so speech does nothing until woken.
      if(parsed.command==='resume'){
        enterStandby(false);setPhase('listening');respond('Resumed. Listening for your message.');return
      }
      if(parsed.command==='stop'){stop();return}
      setPhase('standby');setDetail(`Standby — say “${wakeWord}, resume” to continue.`);return
    }
    if(parsed.command==='standby'){
      enterStandby(true);setPhase('standby')
      respond(`Standby. Still listening; say “${wakeWord}, resume” to continue.`);return
    }
    // A single addressee removes the wake word: while the panel is in chat
    // mode, every plain utterance is a conversation turn — the dictation draft
    // must NOT also hear it. The follow-up window gives the same routing for a
    // few seconds after a spoken reply in any mode. Only plain speech takes
    // this route — a wake-word command keeps its normal meaning, which is what
    // lets "Mux, stop" still kill playback mid-dialog.
    if(parsed.command===null&&onAssistantRef.current&&(assistantChatRef.current?.()||assistantFollowUpActive())){
      const wakeIntent=extractWakeIntent(rawText,statusRef.current?.wake_words?.length?statusRef.current.wake_words:DEFAULT_WAKE_WORDS)
      if(wakeIntent===null&&rawText.trim()){
        setPhase('sending');setDetail('Asking the assistant…')
        try{
          const outcome=await onAssistantRef.current(rawText.trim())
          if(outcome!==false){setPhase('listening');respond(outcome||'Asked the assistant.');return}
        }catch(cause){reportFailure(cause);return}
        setPhase('listening')
      }
    }
    if(parsed.command===null&&onIntentRef.current){
      const spoken=extractWakeIntent(rawText,statusRef.current?.wake_words?.length?statusRef.current.wake_words:DEFAULT_WAKE_WORDS)
      if(spoken!==null){
        try{
          await runRegistryIntent(spoken)
        }catch(cause){reportFailure(cause)}
        return
      }
    }
    if(parsed.command==='cancel'){
      setDraft(clearDraft());setPhase('listening');respond('Voice message cleared. Still listening.');return
    }
    if(parsed.command==='undo'){
      const next=undoUtterance(draftRef.current);setDraft(next);setPhase('listening')
      respond(next.text?'Removed the last phrase.':'Removed the last phrase. Draft is empty.');return
    }
    const pending=appendUtterance(draftRef.current,parsed.text)
    if(pending!==draftRef.current){setDraft(pending);setLandedAt(current=>current+1)}
    if(parsed.command==='send'){
      try{await submit(pending.text)}catch(cause){reportFailure(cause)}
      return
    }
    if(parsed.command==='append'){
      try{await append(pending.text)}catch(cause){reportFailure(cause)}
      return
    }
    if(parsed.command==='mute'){
      bargeInPlayback();setPhase('listening');respond('Playback stopped. Still listening.');return
    }
    if(parsed.command==='read'){
      try{if(await runRegistryIntent('read last reply'))return}catch(cause){reportFailure(cause);return}
      const session=sessionTarget()
      if(!session){setPhase('listening');respond('Read reply needs an agent target. Draft kept.');return}
      if(!statusRef.current?.enabled){setPhase('listening');respond('Read aloud is off. Enable it in Settings, Voice. Still listening.');return}
      setPhase('sending');setDetail('Preparing the latest reply…')
      const streamId=newVoiceStreamId()
      try{
        unlockPlayback();beginRequestedStream(streamId,session.id,'agent')
        const clip=await api<VoiceClip>('POST',`/api/sessions/${session.id}/voice/generate`,{stream_id:streamId})
        await playRequestedStreamFirst(clip.id,clip.stream_id||streamId,session.id,'agent');setPhase('listening');respond('Reply playback started. Still listening.')
      }catch(cause){cancelRequestedStream(streamId);reportFailure(cause)}
      return
    }
    if(parsed.command==='summary'||parsed.command==='verbatim'){
      const session=sessionTarget()
      if(!session){setPhase('listening');respond('Reply mode needs an agent target. Draft kept.');return}
      const mode=parsed.command
      try{
        const updated=await api<Session>('PATCH',`/api/sessions/${session.id}`,{voice_content:mode})
        onSessionRef.current(updated);setPhase('listening');respond(`${mode==='summary'?'Summary':'Verbatim'} replies enabled. Still listening.`)
      }catch(cause){reportFailure(cause)}
      return
    }
    if(parsed.command==='help'){
      try{if(await runRegistryIntent('list voice commands'))return}catch(cause){reportFailure(cause);return}
      setPhase('listening');respond(`${wakeWord} commands: send, cancel, undo, mute, read reply, summary, verbatim, interrupt, standby, resume, and stop.`);return
    }
    if(parsed.command==='comms_on'||parsed.command==='comms_off'){
      await toggleComms(parsed.command==='comms_on')
      return
    }
    if(parsed.command==='interrupt'){
      const session=sessionTarget()
      if(!session){setPhase('listening');respond('Interrupt needs an agent target. Draft kept.');return}
      bargeInPlayback();setPhase('listening');respond('Playback stopped. Interrupt sent to the agent.')
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

  type Decoded={parsed:ParsedMuxVoice;text:string;postAt:number;responseAt:number;server:ServerTimings}
  const decode=async(audio:Blob,marks:CaptureMarks,profile:'command'|'dictation',signal?:AbortSignal):Promise<Decoded>=>{
    const postAt=performance.now()
    const response=await fetch('/api/voice/transcribe',{
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
    const text=String(payload.text||'')
    return {parsed:matcherRef.current.parse(text),text,postAt,responseAt,server:payload.timings||{}}
  }

  const transcribe=async(audio:Blob,marks:CaptureMarks)=>{
    if(!enabledRef.current)return
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
    try{
      recordHistory('you',decoded.text)
      if(marks.playbackAtStart&&!isPlaybackControl(decoded.parsed.command)){
        const spoken=marks.playbackOriginAtStart==='system'
          ?extractWakeIntent(decoded.text,statusRef.current?.wake_words?.length?statusRef.current.wake_words:DEFAULT_WAKE_WORDS)
          :null
        if(spoken!==null&&safeDuringSystemPlayback(spoken)){
          bargeInPlayback()
          await handleTranscript({command:null,text:''},decoded.text)
        }else{
          setPhase('listening')
          respond(marks.playbackOriginAtStart==='system'
            ?spoken===null
              ?`Playback command ignored. Say “${wakeRef.current}” before a read-only lookup or navigation command.`
              :'Playback command ignored. During application speech, only read-only lookup and navigation commands are allowed.'
            :`Playback echo ignored. Say “${wakeRef.current}, mute” before another command.`)
        }
      }else await handleTranscript(decoded.parsed,decoded.text)
    }
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
    if(!enabledRef.current||!capture||standbyRef.current||marks.playbackAtStart)return
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
    recordHistory('you',decoded.text)
    try{await handleTranscript(decoded.parsed,decoded.text)}
    finally{
      reportLatency(buildLatencySample({
        marks:{...marks,speculative:true},postAt:decoded.postAt,responseAt:decoded.responseAt,
        actionAt:performance.now(),server:decoded.server,command:decoded.parsed.command||'',
      }))
    }
  }

  // Talk mode is mic → transcribe → named target. It does not require read aloud and never
  // changes it: the tts chip, the device autoplay toggle, and this button are three
  // independent switches. Only the read/summary/verbatim voice commands need TTS,
  // and each reports that itself.
  const start=async()=>{
    if(enabledRef.current||startingRef.current)stop()
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
      playbackOrigin:()=>getPlayback().origin,
      onPlaybackProbe:active=>setPlaybackDucked(active),
      onPlaybackProbeResult:result=>{void api('POST','/api/voice/barge-in-diagnostic',result,{timeoutMs:4000}).catch(()=>{})},
      onSpeechStart:()=>{if(!enabledRef.current)return;if(standbyRef.current){setPhase('standby');return}setPhase('hearing');setDetail(getPlayback().playing?'Listening through playback…':'Listening…')},
      onBargeIn:()=>{bargeInPlayback();if(enabledRef.current&&!standbyRef.current){setPhase('hearing');setDetail('Playback stopped. Listening…')}},
      // Fired at the endpoint, before any text exists. It is the whole point of the
      // phase: the answer has to arrive when the user stops talking, not when the
      // decode does, or the pause reads as a failure.
      onSpeechEnd:()=>{if(!enabledRef.current||standbyRef.current)return;playEarcon('heard');setPhase('heard');setDetail('Heard you.')},
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
    startingRef.current=capture
    try{
      await capture.start()
      // A second toggle during the permission prompt already stopped or replaced this
      // attempt. The losing capture must release the device instead of reviving Talk.
      if(startingRef.current!==capture){capture.stop();return}
      startingRef.current=null
      captureRef.current=capture;enabledRef.current=true;enterStandby(false);setActive(true)
      // Always warming here: `start` fires the detector load without awaiting it, and
      // nothing between that and this line yields, so `onDetector` cannot have run
      // yet. The displayed phase derives `warming` from `detector` being null, so
      // readiness has one source of truth rather than a phase and a flag to disagree.
      setPhase('listening');setDetail(WARMING_DETAIL)
    }catch(cause){capture.stop();if(startingRef.current!==capture)return;startingRef.current=null;enabledRef.current=false;setActive(false);reportFailure(cause)}
  }

  return {
    target,targetAvailable,pinned:!!pinnedTarget,
    // Derived rather than stored: readiness has one source of truth (`detector`), and
    // an idle phase that claimed `listening` before the detector loaded would be true
    // and still misleading — capture is listening, on the fallback's 900 ms tail.
    phase:phase==='listening'&&detector===null?'warming':phase,
    detail,draft:draft.text,active,standby,comms:!!commsTargetId,wake,landedAt,latency,detector,history,
    toggle:()=>{
      if(enabledRef.current||startingRef.current||phase!=='off'){stop();return}
      void start()
    },
    togglePin:()=>setPinnedTarget(current=>toggleConversationTargetPin(current,targetRef.current)),
    stop,
    send:()=>{void (async()=>{try{await submit(draftRef.current.text)}catch(cause){reportFailure(cause)}})()},
    append:()=>{void (async()=>{try{await append(draftRef.current.text)}catch(cause){reportFailure(cause)}})()},
    undo:()=>{
      const next=undoUtterance(draftRef.current);setDraft(next)
      setDetail(next.text?'Removed the last phrase.':'Removed the last phrase. Draft is empty.')
    },
    clear:()=>{setDraft(clearDraft());setDetail('Draft cleared. Still listening.')},
    clearHistory:()=>{setHistory([]);saveVoiceConversationHistory([])},
    toggleStandby:()=>{
      const next=!standbyRef.current
      enterStandby(next)
      if(enabledRef.current)setPhase(next?'standby':'listening')
      setDetail(next?`Standby. Still listening; say “${wakeRef.current}, resume” to continue.`:'Resumed. Listening for your message.')
    },
    toggleComms:()=>{void toggleComms()},
    edit:text=>{setDraft(editDraft(text))},
  }
}

// One glyph, two states. The slash is the whole "off" signal now that the button
// carries no word, so it has to survive at 12px: a diagonal over the mic reads at
// that size where a redrawn muted-mic body does not.
function MicIcon({slashed}:{slashed:boolean}){
  return <svg viewBox="0 0 24 24" aria-hidden="true">
    <rect x="8" y="3" width="8" height="12" rx="4"/>
    <path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6"/>
    {slashed&&<path d="M4 3.5l16 17"/>}
  </svg>
}

/**
 * The workspace-level Talk toggle. App places it immediately before Run in each toolbar.
 *
 * Icon only, and lit only while capture is actually running. It used to wear the
 * green chip in every state, which made the one thing a microphone control has to
 * answer - "is this listening to me right now?" - unreadable from the button, and
 * "Talk" alongside the mic said nothing the mic did not. Off is the same recessed
 * treatment as the unconfigured state, because both mean "not listening"; the
 * slashed glyph is what distinguishes off from on, and `aria-pressed` plus the
 * label carry it for anyone not reading the glyph.
 */
export function ConversationToggle({
  conversation,
  configured,
}:{
  conversation:Conversation
  configured:boolean
}){
  const active=conversation.phase!=='off'
  const label=!configured
    ? 'Set up hands-free conversation'
    : active
      ? 'Stop hands-free conversation'
      : 'Start hands-free conversation'
  return <button
    class={`conversation-talk-toggle ${active?'active':'off'}${configured?'':' setup'}`}
    aria-label={label}
    aria-pressed={configured?active:undefined}
    title={!configured
      ? 'Microphone conversation is disabled - open Voice settings'
      : `${label}${conversation.target?` - target: ${conversation.target.label}`:' - focus an agent or text surface to choose a target'}`}
    // Unconfigured, the toggle's job is to reach the microphone switch itself rather than
    // the Voice tab it sits several sections down in.
    onClick={configured?()=>conversation.toggle():()=>requestSetting('voice.stt')}
  ><MicIcon slashed={!active}/></button>
}

export type VoicePanelMode='dictation'|'chat'

/**
 * The global voice surface: the dictation draft card, and (toggleable inside
 * the same floating panel) the assistant conversation view. It renders while
 * capture runs, and also with the microphone off when the chat was opened
 * explicitly — the assistant is voice-first, not voice-only.
 */
export function ConversationSurface({
  conversation,
  commands,
  configuredCommands,
  onOpenSettings,
  placement='fallback',
  mode='dictation',
  onMode,
  assistantView,
  assistantOpen=false,
  onCloseAssistant,
}:{
  conversation:Conversation
  commands:Command[]
  configuredCommands?:{action:string;phrases:string[]}[]
  onOpenSettings:()=>void
  placement?:'pane'|'fallback'
  mode?:VoicePanelMode
  onMode?:(mode:VoicePanelMode)=>void
  assistantView?:ComponentChildren
  assistantOpen?:boolean
  onCloseAssistant?:()=>void
}){
  const talkActive=conversation.phase!=='off'
  if(!talkActive&&!assistantOpen)return null
  const effectiveMode:VoicePanelMode=talkActive?mode:'chat'
  const panel=<DictationPanel
    conversation={conversation}
    commands={commands}
    configuredCommands={configuredCommands}
    onOpenSettings={onOpenSettings}
    mode={effectiveMode}
    onMode={onMode}
    assistantView={assistantView}
    onCloseAssistant={onCloseAssistant}
  />
  return placement==='pane'?panel:<div class="conversation-layer active">{panel}</div>
}

/**
 * The app-level dictation state can render in a pane overlay without belonging to that pane.
 * Moving the view follows focus; capture, history, target pinning, and the draft stay alive.
 */
export function DictationPanel({
  conversation,commands,configuredCommands,onOpenSettings,
  mode='dictation',onMode,assistantView,onCloseAssistant,
}:{
  conversation:Conversation
  commands:Command[]
  configuredCommands?:{action:string;phrases:string[]}[]
  onOpenSettings:()=>void
  mode?:VoicePanelMode
  onMode?:(mode:VoicePanelMode)=>void
  assistantView?:ComponentChildren
  onCloseAssistant?:()=>void
}){
  const draftRef=useRef<HTMLTextAreaElement|null>(null)
  const historyRef=useRef<HTMLDivElement|null>(null)
  const [landed,setLanded]=useState(false)
  const [historyOpen,setHistoryOpen]=useState(()=>loadVoiceConversationHistoryOpen())
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
  useLayoutEffect(()=>{
    if(historyOpen&&historyRef.current)historyRef.current.scrollTop=historyRef.current.scrollHeight
  },[conversation.history.length,historyOpen])
  const toggleHistory=()=>setHistoryOpen(value=>{
    const next=!value
    saveVoiceConversationHistoryOpen(next)
    return next
  })
  const send=()=>conversation.send()
  const talkActive=conversation.phase!=='off'
  const chat=mode==='chat'
  const modeToggle=(assistantView!==undefined&&onMode)?<div class="voice-mode-toggle" role="tablist" aria-label="Voice panel mode">
    <button role="tab" aria-selected={!chat} class={chat?'':'active'} disabled={!talkActive} title={talkActive?'Dictation draft and Talk history':'Start Talk to dictate'} onClick={()=>onMode('dictation')}>talk</button>
    <button role="tab" aria-selected={chat} class={chat?'active':''} title="Converse with the Mux assistant" onClick={()=>onMode('chat')}>chat</button>
  </div>:null
  if(chat){
    // Chat mode: the same floating panel, taller, holding the assistant
    // conversation. Capture (when running) keeps listening underneath it —
    // the mode changes what the panel shows, never what the microphone does.
    return <section class={`dictation-panel chat-mode ${conversation.phase}`} aria-label="Mux assistant conversation">
      <header>
        {modeToggle}
        {talkActive&&<span class={`dictation-phase ${conversation.phase}`} title={`${conversation.detail?conversation.detail+' · ':''}In chat mode the microphone addresses the assistant: plain speech becomes a conversation turn and never lands in the dictation draft. Wake-word commands keep their normal meaning.`}>talk:{conversation.phase==='transcribing'?'typing':conversation.phase}</span>}
        {talkActive&&<span class="dictation-mic-note" title="Plain speech goes to the assistant while chat mode is open">mic→assistant</span>}
        <div class="dictation-actions">
          {talkActive&&<button class="dictation-stop" title="Stop dictating and release the microphone" onClick={()=>conversation.stop()}>stop mic</button>}
          <VoiceCommandsButton commands={commands} configuredCommands={configuredCommands}/>
          <button class="dictation-settings" aria-label="Open Voice settings" title="Open Voice settings" onClick={onOpenSettings}>⚙</button>
          {!talkActive&&onCloseAssistant&&<button class="dictation-stop" title="Close the assistant panel" onClick={onCloseAssistant}>close</button>}
        </div>
      </header>
      {assistantView}
    </section>
  }
  return <section class={`dictation-panel ${conversation.phase}${landed?' landed':''}`} aria-label="Voice dictation draft">
    <header>
      {modeToggle}
      <span
        class={`dictation-phase ${conversation.phase}`}
        title={`${conversation.detail}${conversation.detail?' · ':''}${conversation.detector===null
          ? 'The speech detector is still loading. Capture is already listening, but on the fallback detector, so an utterance needs a longer pause to end. Hold Ctrl+Alt+Space to talk with no pause detection at all.'
          : conversation.detector==='silero'
            ? 'Silero voice activity detection: an utterance ends after a short pause. Hold Ctrl+Alt+Space to talk with no pause detection at all.'
            : 'Energy-based detection: an utterance needs a longer pause to end. Hold Ctrl+Alt+Space to talk with no pause detection at all.'}`}
      >talk:{conversation.phase==='transcribing'?'typing':conversation.phase}</span>
      <span class="sr-only" role="status" aria-live="polite">{conversation.detail}</span>
      {conversation.latency&&<span class="dictation-latency" title={`End of speech to action — ${formatLatency(conversation.latency)}`}>{Math.round(conversation.latency.total_ms)} ms</span>}
      <div class="dictation-actions">
        <button class="dictation-send" title="Commit the draft to the named target (Ctrl+Enter)" disabled={!conversation.draft.trim()||!conversation.targetAvailable} onClick={send}>send</button>
        <button title="Append the draft to the target without submitting" disabled={!conversation.draft.trim()||!conversation.targetAvailable} onClick={()=>conversation.append()}>append</button>
        <button title="Remove the last phrase that was heard" disabled={!conversation.draft} onClick={()=>conversation.undo()}>undo</button>
        <button title="Clear the draft" disabled={!conversation.draft} onClick={()=>conversation.clear()}>clear</button>
        <button class={conversation.standby?'active':''} title={conversation.standby?'Resume listening':'Keep the mic open but ignore speech until resumed'} onClick={()=>conversation.toggleStandby()}>{conversation.standby?'resume':'standby'}</button>
        <button class={conversation.comms?'active':''} aria-pressed={conversation.comms} title={conversation.comms?'Exit short spoken agent replies and restore prior read-aloud settings':'Pin this agent and request short spoken replies'} onClick={()=>conversation.toggleComms()}>comms:{conversation.comms?'on':'off'}</button>
        <button class="dictation-stop" title="Stop dictating and release the microphone" onClick={()=>conversation.stop()}>stop</button>
        <VoiceCommandsButton commands={commands} configuredCommands={configuredCommands}/>
        <button class="dictation-settings" aria-label="Open Voice settings" title="Open Voice settings (engine, wake words, commands)" onClick={onOpenSettings}>⚙</button>
      </div>
    </header>
    <div class={`dictation-target${conversation.targetAvailable?'':' unavailable'}`}>
      <span>to:</span>
      <strong title={conversation.target?.label||'No target'}>{conversation.target?.label||'No target focused'}</strong>
      <button
        class={conversation.pinned?'active':''}
        aria-pressed={conversation.pinned}
        disabled={!conversation.target}
        title={conversation.pinned?'Follow workspace focus again':'Stay on this target while focus moves'}
        onClick={()=>conversation.togglePin()}
      >{conversation.pinned?'unpin':'pin'}</button>
    </div>
    <section class={`conversation-history-shell${historyOpen?'':' collapsed'}`} aria-label="Talk transcript history">
      <header>
        <button class="conversation-history-toggle" aria-expanded={historyOpen} aria-controls="talk-transcript-history" onClick={toggleHistory}>
          <span class="conversation-history-caret" aria-hidden="true">{historyOpen?'▾':'▸'}</span>
          <strong>Talk history</strong>
          <span>{conversation.history.length} message{conversation.history.length===1?'':'s'}</span>
        </button>
        <button class="conversation-history-clear" disabled={!conversation.history.length} onClick={()=>conversation.clearHistory()}>clear</button>
      </header>
      {historyOpen&&<div id="talk-transcript-history" ref={historyRef} class="conversation-history" role="log" aria-live="polite">
        {conversation.history.length?conversation.history.map(entry=><article key={entry.id} class={entry.role}>
          <header><strong>{entry.role}</strong><time dateTime={new Date(entry.at).toISOString()}>{new Date(entry.at).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}</time></header>
          <p>{entry.text}</p>
        </article>):<p class="conversation-history-empty">Your transcripts and Mux responses will appear here.</p>}
      </div>}
    </section>
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
