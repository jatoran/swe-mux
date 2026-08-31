import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { requestSetting } from './settingTargets'
import { buildVoiceMatcher, conversationCapability, DEFAULT_COMMANDS, DEFAULT_WAKE_WORDS, HOLD_ENTER_PHRASES, HOLD_RELEASE_PHRASES, isPlaybackControl, matchesBarePhrase, PersistentVoiceCapture, playbackTranscriptVerdict } from './conversation'
import { captureFailureNote, desktopMediaReport } from './desktopShell'
import type { CaptureDetector, ParsedMuxVoice } from './conversation'
import { appendUtterance, clearDraft, editDraft, EMPTY_DRAFT, undoUtterance } from './conversationDraft'
import type { Draft } from './conversationDraft'
import { enableMobileVoice, mobileVoiceDestination } from './mobileVoice'
import { buildLatencySample, formatLatency } from './voiceLatency'
import type { CaptureMarks, LatencySample, ServerTimings } from './voiceLatency'
import type { Session, VoiceClip, VoiceStatus } from './types'
import {
  autoplayEnabled, bargeInPlayback, beginRequestedStream, cancelRequestedStream, getPlayback,
  newVoiceStreamId, playClipGroup, playRequestedStreamFirst, setAutoplayEnabled,
  setPinnedPlaybackSession,
  setPlaybackDucked, unlockPlayback,
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
import { assistantFollowUpActive, latestOpenAction, spokenConfirmation } from './assistant'
import { chatPatienceMs, endpointPatienceMs } from './utteranceCompleteness'
import { DEFERRAL_PARK_MAX_MS, DeferralPen, pendingUtterance } from './utteranceDeferral'
import type { DeferralSource, DeferredUtterance } from './utteranceDeferral'
import { playEarcon } from './earcons'
import {
  canCollapseVoiceDock, canExpandVoiceDock, effectiveVoicePanelMode, voiceBodyVariant,
} from './voiceDock'
import type { VoiceDockState, VoicePanelMode } from './voiceDock'
import type { ComponentChildren, JSX } from 'preact'

// `heard` exists to answer the user the instant the endpoint fires, before any text
// can exist. Silence after speaking reads as broken; the same silence after an
// acknowledgement reads as thinking.
//
// `warming` is never set directly: it is derived, and reported while the microphone is
// open but the speech detector has not finished loading. Capture is genuinely
// listening in that window — on the energy detector and its 900 ms tail — so calling
// it `listening` would be true and still misleading, and calling it `starting` would
// be a lie in the other direction.
// 'stalled' is the watchdog's phase: capture claims to run but no audio frames are
// arriving, so "listening" would be a lie. It is not 'error' — capture may recover.
type Phase='off'|'starting'|'warming'|'listening'|'hearing'|'heard'|'transcribing'|'sending'|'standby'|'stalled'|'error'

// The first configured wake word, shown in prompts so the hints match the user's
// own trigger word rather than a hardcoded "Mux".
const primaryWake=(words?:string[])=>(words&&words.find(word=>word.trim())||DEFAULT_WAKE_WORDS[0])

/**
 * How a deferred fragment left the holding pen; reported for every deferral.
 *
 * The pair that matters is `merged` versus `submitted`: the first means the
 * trigger caught a real trail-off, the second means the operator was finished
 * after all and the word cost them one extension, so the ratio of the two IS the
 * heuristic's false-positive rate. `held` and `discarded` are neither and are
 * counted apart so they cannot be mistaken for either verdict.
 */
type DeferralOutcome='merged'|'submitted'|'discarded'|'held'

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
  /** Brainstorm hold (chat mode): transcription continues, replies wait for a cue. */
  hold:boolean
  /** Speech accumulated while holding; released as one assistant turn. */
  holdBuffer:string
  /**
   * The dangling token that is currently holding a chat turn back, or ''.
   * At most one utterance is ever deferred, so this is a single value rather
   * than a queue, and it clears the moment the turn resolves.
   */
  deferredTrigger:string
  /**
   * Who held it: the word heuristic, or the assistant reporting the turn had
   * nothing answerable in it. The chip reads differently for the two, because
   * only one of them ever submits the fragment on its own.
   */
  deferredSource:DeferralSource|null
  /**
   * The operator's in-flight words, for the conversation panel's pending row:
   * the brainstorm buffer, the fragment the pen is holding, and the speculative
   * reading of the breath happening right now, composed in the order they were
   * spoken. Client-local - the daemon has seen none of it yet.
   */
  pendingText:string
  /** Header for that row, naming which of the three states produced it. */
  pendingNote:string
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
  /** Send the held brainstorm to the assistant now (the “go ahead” cue). */
  releaseHold:()=>void
  /** Drop the held brainstorm and leave hold mode. */
  discardHold:()=>void
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
  /**
   * Re-read `/api/voice` and return it, for the press that finds no status at all.
   *
   * `status` is fetched once when the app mounts. A page that lost that fetch
   * used to refuse every press for the rest of its life while claiming the
   * daemon was at fault. That is the diagnosis for a microphone dead in the
   * desktop shell - the one client whose page is opened once and kept for days -
   * and healthy in a browser tab against the same daemon.
   */
  refreshStatus?:()=>Promise<VoiceStatus|null>,
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
  const [hold,setHoldState]=useState(false)
  const [holdBuffer,setHoldBufferState]=useState('')
  const [deferredTrigger,setDeferredTrigger]=useState('')
  const [deferredSource,setDeferredSource]=useState<DeferralSource|null>(null)
  /**
   * The speculative decode's provisional reading of the breath in progress.
   *
   * The real transcript cannot exist until the endpoint has proved the turn is
   * over, which is patience plus any extension later - measured at 3.2 s of a
   * 4.1 s round trip on a held fragment. The speculative decode already ran at
   * 160 ms of silence and its text was thrown away for anything that was not a
   * command; showing it costs no extra decode and is the whole latency win.
   */
  const [provisional,setProvisional]=useState('')
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
  const holdRef=useRef(false)
  const holdBufferRef=useRef('')
  /**
   * The holding pen for one unfinished utterance (`utteranceDeferral.ts`), and
   * the release timer that empties it.
   *
   * The pen owns the decisions and this component owns the effects: arming the
   * timer, reporting the outcome, and dispatching the turn. A `useRef` and not
   * state, because the capture callbacks that read it outlive the render that
   * built them.
   */
  const penRef=useRef(new DeferralPen())
  const deferralTimerRef=useRef<ReturnType<typeof setTimeout>|null>(null)
  /**
   * A park has no release timer - it is waiting for speech, not for a clock - so
   * this only exists to stop a fragment the operator abandoned from sitting in
   * the pen forever and gluing itself to whatever they say an hour later.
   */
  const parkTimerRef=useRef<ReturnType<typeof setTimeout>|null>(null)
  /**
   * The exact body most recently sent to the assistant.
   *
   * The send call returns as soon as the daemon accepts the turn, so the "there
   * was nothing to answer" verdict arrives later on the event socket with no copy
   * of the text in it. Voice dispatches one turn at a time, so one slot is enough.
   */
  const lastAskedRef=useRef('')
  /**
   * The utterance whose real transcript has already landed.
   *
   * A speculative decode starts earlier but can finish later, and a provisional
   * reading published after the accurate one would replace good text with worse
   * text and then duplicate it against the fragment the pen just took. One slot
   * is enough: utterances are strictly serial.
   */
  const settledUtteranceRef=useRef('')
  /** Clear the provisional row, and refuse any late speculation for this utterance. */
  const settleProvisional=(utteranceId:string)=>{
    settledUtteranceRef.current=utteranceId
    setProvisional('')
  }
  /** Speech is arriving right now, per the gate. Read by the release timer. */
  const speakingRef=useRef(false)
  /** How many utterances are mid-decode. A continuation in flight is not silence. */
  const decodingRef=useRef(0)
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
  const refreshStatusRef=useRef(refreshStatus);refreshStatusRef.current=refreshStatus
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
  // The stall lines are compared exactly on recovery, like WARMING_DETAIL above:
  // the recovered message replaces them only while they are still on screen.
  const STALL_DETAIL='Microphone stalled — no audio is arriving. Trying to revive it…'
  const STALL_ENDED_DETAIL='The browser released the microphone. Turn Talk off and on to reconnect.'

  const setDraft=(next:Draft)=>{draftRef.current=next;setDraftState(next)}
  const enterStandby=(value:boolean)=>{standbyRef.current=value;setStandby(value)}
  const enterHold=(value:boolean)=>{holdRef.current=value;setHoldState(value)}
  const setHoldBuffer=(text:string)=>{holdBufferRef.current=text;setHoldBufferState(text)}
  const recordHistory=(role:VoiceConversationRole,text:string)=>setHistory(current=>{
    const next=appendVoiceConversationEntry(current,role,text)
    if(next!==current)saveVoiceConversationHistory(next)
    return next
  })
  const respond=(text:string)=>{setDetail(text);recordHistory('mux',text)}

  /**
   * Report a resolved deferral to the daemon, with the token that triggered it.
   *
   * Sent on resolution rather than at the deferral, because the outcome is what
   * makes the heuristic measurable: `merged` means the trigger caught a real
   * trail-off, `submitted` means the operator was finished after all and the
   * word cost them one extension. Fire-and-forget - a diagnostic that can fail
   * an utterance is worse than no diagnostic.
   */
  const reportDeferral=(deferred:DeferredUtterance,outcome:DeferralOutcome)=>{
    void api('POST','/api/voice/deferral-diagnostic',{
      outcome,trigger:deferred.trigger,kind:deferred.kind,words:deferred.words,
      heldMs:Math.round(performance.now()-deferred.at),
      // The score and the window it bought. Without these the diagnostic can say
      // which trigger fired but not whether its prior was worth what it cost,
      // which is the only question the continuous curve raises.
      source:deferred.source,completion:deferred.completion,extensionMs:deferred.extensionMs,
    },{timeoutMs:4000}).catch(()=>{})
  }
  /** Cancel a pending release and clear the chip. Safe to call with nothing held. */
  const clearDeferralTimer=()=>{
    if(deferralTimerRef.current!==null)clearTimeout(deferralTimerRef.current)
    deferralTimerRef.current=null
    if(parkTimerRef.current!==null)clearTimeout(parkTimerRef.current)
    parkTimerRef.current=null
    setDeferredTrigger('');setDeferredSource(null)
  }
  /** Empty the pen for a non-dispatch reason, reporting the deferral it ends. */
  const resolveDeferral=(outcome:DeferralOutcome):DeferredUtterance|null=>{
    const deferred=penRef.current.take()
    clearDeferralTimer()
    if(deferred)reportDeferral(deferred,outcome)
    return deferred
  }

  const stop=(record=true)=>{
    bargeInPlayback()
    if(record&&enabledRef.current)recordHistory('mux','Talk stopped.')
    enabledRef.current=false;enterStandby(false);enterHold(false);setHoldBuffer('')
    // A held fragment dies with the microphone. Submitting it after the operator
    // stopped Talk would answer a question they withdrew. `speakingRef` goes with
    // it: a microphone released mid-utterance never raises the endpoint that
    // would clear it, and a stale "still speaking" would make the next
    // deferral's release re-arm against nothing.
    resolveDeferral('discarded')
    // The provisional row dies with the microphone too: it is a reading of a
    // breath nobody is going to finish, and leaving it on screen would claim the
    // assistant is still listening to it.
    setProvisional('')
    speakingRef.current=false
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
      // Alt+Shift, not Ctrl+Alt: AltGr synthesises Ctrl+Alt on most non-US layouts,
      // so the old chord began capture whenever such a user typed AltGr+Space. This
      // is a *hold* rather than a command, so it cannot be an ordinary binding - the
      // rule model has no press-and-hold - which is why it is fixed here.
      if(event.code!=='Space'||!event.altKey||!event.shiftKey||event.ctrlKey||event.repeat)return
      if(!enabledRef.current||!captureRef.current)return
      event.preventDefault();event.stopPropagation()
      begin()
    }
    const up=(event:KeyboardEvent)=>{
      // Any part of the chord releasing ends the phrase; the modifiers and the key
      // are rarely released in the same order twice.
      if(event.code==='Space'||event.key==='Shift'||event.key==='Alt')end()
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
      setPinnedPlaybackSession(activeId,false)
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
    // Playback is otherwise focus-driven, and Voice Comms is the one mode where that
    // is wrong: the operator is talking to *this* agent hands-free, so its replies
    // have to speak even while their eyes are on another pane. The pin is released
    // when comms is turned off, above.
    setPinnedPlaybackSession(target.id,true)
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

  // ---- Brainstorm hold: chat mode keeps transcribing but stops answering. ----
  // Deterministic client state, never a model decision: the annoyance is turns
  // firing at every pause, and only the client can decline to send the next one.
  const chatAddressee=()=>!!onAssistantRef.current&&(assistantChatRef.current?.()===true||assistantFollowUpActive())
  /**
   * An assistant confirmation card is open and this device would route the
   * answer to it. That state changes what the microphone is waiting for: the
   * operator is answering a question, not composing a thought, so the endpoint
   * stops being patient and a recognized verdict is treated as a complete
   * utterance the way a wake-worded command is.
   */
  const awaitingVerdict=()=>chatAddressee()&&latestOpenAction()!==null
  /** The operator's own chat patience, clamped; one number the deferral derives from. */
  const configuredPatience=()=>chatPatienceMs(statusRef.current?.chat_patience_ms)

  // ---- Unfinished-utterance deferral: a trailed-off clause is not a question. ----
  // Deterministic and pre-model by design. The heuristic runs BEFORE a chat turn
  // is dispatched, an utterance ending mid-clause earns exactly ONE patience
  // extension instead of submitting, and the model is never asked to decide
  // whether the operator was finished - a model told to sometimes return nothing
  // returns nothing when it should have answered. The daemon's queue-merge
  // remains the safety net for fragments this rule set does not recognize.

  /** Send one body to the assistant and report it, shared by both dispatch paths. */
  const askAssistant=async(body:string):Promise<boolean>=>{
    const handler=onAssistantRef.current
    if(!handler)return false
    setPhase('sending');setDetail('Asking the assistant…')
    lastAskedRef.current=body
    const outcome=await handler(body)
    if(outcome===false){setPhase('listening');return false}
    setPhase('listening');respond(outcome||'Asked the assistant.')
    return true
  }
  /**
   * The release timer fired. The pen decides; this only carries out the verdict.
   *
   * `busy` is "the operator is still mid-thought": speech arriving, or an
   * utterance mid-decode. The pen re-arms rather than submitting then, because
   * answering half a sentence whose other half is already in flight is the
   * precise failure being fixed - bounded by its own hold ceiling, so a detector
   * wedged in "speaking" cannot hold a turn forever.
   */
  const releaseDeferral=(deferred:DeferredUtterance,holdMs:number)=>{
    const busy=speakingRef.current||decodingRef.current>0
    const verdict=penRef.current.release(deferred,busy)
    if(verdict.kind==='gone')return
    if(verdict.kind==='wait'){
      deferralTimerRef.current=setTimeout(()=>releaseDeferral(deferred,holdMs),holdMs)
      return
    }
    clearDeferralTimer()
    reportDeferral(verdict.deferred,'submitted')
    void (async()=>{
      if(!enabledRef.current)return
      try{await askAssistant(verdict.deferred.text)}
      catch(cause){reportFailure(cause)}
    })()
  }
  /** Arm the single extension the pen just granted, and say why on screen. */
  const armDeferral=(deferred:DeferredUtterance)=>{
    // The pen's window, not a freshly computed one: the score that justified the
    // hold also sized it, and recomputing here is how the gate's tail and the
    // release timer drift apart.
    const holdMs=deferred.extensionMs
    deferralTimerRef.current=setTimeout(()=>releaseDeferral(deferred,holdMs),holdMs)
    setDeferredTrigger(deferred.trigger);setDeferredSource('heuristic')
    setPhase('listening')
    setDetail(`That sounded unfinished after “${deferred.trigger}” - keep going, or pause and it goes as-is.`)
  }
  /**
   * The assistant reported that the turn it was just given had nothing in it to
   * answer. Park the text and say nothing at all.
   *
   * No timer, no chip flash, no spoken line: the operator is mid-thought and the
   * only correct output is silence. Their next breath merges with this fragment
   * and the joined text goes as one turn. The stale-park timeout is the only
   * clock involved, and it exists to forget an abandoned fragment rather than to
   * answer it.
   */
  const parkAssistantHold=(text:string)=>{
    if(penRef.current.pending)return
    const parked=penRef.current.park(text,configuredPatience())
    if(!parked)return
    setDeferredTrigger(parked.trigger);setDeferredSource('assistant')
    setPhase('listening')
    setDetail('Still listening - that read as half a thought, so it is waiting for the rest.')
    parkTimerRef.current=setTimeout(()=>{
      const expired=penRef.current.expireStalePark()
      if(expired)reportDeferral(expired,'discarded')
      clearDeferralTimer()
    },DEFERRAL_PARK_MAX_MS)
  }
  const beginHold=()=>{
    // A fragment held by the heuristic is thinking-out-loud text, which is
    // exactly what the brainstorm buffer is for: fold it in rather than
    // answering it behind the operator's back.
    const pending=resolveDeferral('held')
    if(pending)setHoldBuffer([holdBufferRef.current,pending.text].map(part=>part.trim()).filter(Boolean).join(' '))
    enterHold(true);setPhase('listening')
    respond('Holding — thinking out loud is buffered, not answered. Say “go ahead” when you want the assistant to respond.')
  }
  // The assistant's own completeness verdict, arriving on the event socket.
  //
  // Subscribed here rather than threaded through `onAssistantUtterance` because
  // that call returns when the daemon *accepts* the turn, long before the model
  // has decided anything. The daemon suppresses a held turn's text, speech, and
  // stored message; this is the half that keeps the operator's words, so the next
  // breath has something to join.
  const parkRef=useRef(parkAssistantHold);parkRef.current=parkAssistantHold
  useEffect(()=>{
    const handler=(raw:Event)=>{
      const detail=(raw as CustomEvent).detail as {type:string;payload:Record<string,unknown>;replay?:boolean}
      if(!detail||detail.replay)return
      if(detail.type!=='assistant_turn_done'&&detail.type!=='assistant_turn_failed')return
      const asked=lastAskedRef.current
      lastAskedRef.current=''
      if(detail.type!=='assistant_turn_done')return
      if(detail.payload?.held!==true||!asked)return
      parkRef.current(asked)
    }
    window.addEventListener('mux:assistant-event',handler)
    return()=>window.removeEventListener('mux:assistant-event',handler)
  },[])

  const releaseHold=async(extra='')=>{
    const combined=[holdBufferRef.current,extra].map(part=>part.trim()).filter(Boolean).join(' ')
    if(!combined){enterHold(false);setHoldBuffer('');setPhase('listening');respond('Nothing was buffered. Chat replies normally again.');return}
    if(!onAssistantRef.current){enterHold(false);setHoldBuffer('');setPhase('listening');respond('The assistant is unavailable; the brainstorm stays in the Talk history.');return}
    // Kept until the turn is accepted: a failure below must never cost the
    // buffer — the whole point of holding was that this text took minutes.
    setHoldBuffer(combined)
    setPhase('sending');setDetail('Sending your brainstorm to the assistant…')
    try{
      const outcome=await onAssistantRef.current(combined)
      if(outcome===false){
        setPhase('listening')
        respond('Chat mode is off, so the brainstorm is kept. Reopen chat and say “go ahead”.')
        return
      }
      enterHold(false);setHoldBuffer('')
      setPhase('listening');respond(outcome||'Sent the brainstorm to the assistant.')
    }catch(cause){reportFailure(cause)}
  }
  const discardHold=()=>{
    enterHold(false);setHoldBuffer('')
    setPhase('listening');respond('Brainstorm discarded. Chat replies normally again.')
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
      // Standby and cancel are both "stop paying attention to what I just said",
      // so a fragment held by the heuristic goes with them rather than surfacing
      // as a turn seconds after the operator asked for quiet.
      resolveDeferral('discarded')
      setProvisional('')
      enterStandby(true);setPhase('standby')
      respond(`Standby. Still listening; say “${wakeWord}, resume” to continue.`);return
    }
    // Brainstorm hold: transcription continues, plain speech buffers, and the
    // assistant answers only on a proceed cue. Wake-worded commands keep their
    // meaning throughout — anything this branch does not own falls through, so
    // "Mux, stop" still works mid-brainstorm.
    if(holdRef.current){
      if(parsed.command==='proceed'){await releaseHold(parsed.text);return}
      if(parsed.command==='hold'){setPhase('listening');respond('Already holding. Keep thinking; say “go ahead” when done.');return}
      if(parsed.command==='cancel'){setHoldBuffer('');setPhase('listening');respond('Brainstorm cleared. Still holding.');return}
      if(parsed.command===null){
        const wakeIntent=extractWakeIntent(rawText,statusRef.current?.wake_words?.length?statusRef.current.wake_words:DEFAULT_WAKE_WORDS)
        if(wakeIntent===null){
          if(matchesBarePhrase(rawText,HOLD_RELEASE_PHRASES)){await releaseHold();return}
          const combined=[holdBufferRef.current,rawText.trim()].filter(Boolean).join(' ')
          setHoldBuffer(combined)
          setPhase('listening')
          setDetail(`Holding — ${combined?combined.split(/\s+/).length:0} words buffered. Say “go ahead” when done.`)
          return
        }
        // A wake-worded free intent ("mux, open project x") keeps working while
        // holding; it falls through to the registry branch below.
      }
    }
    if(parsed.command==='hold'){
      if(!chatAddressee()){
        setPhase('listening')
        respond('Hold is for chat mode — switch the voice panel to chat first. In talk mode the draft already waits for “send”.')
        return
      }
      beginHold();return
    }
    if(parsed.command==='proceed'){
      setPhase('listening');respond('Nothing is on hold. Chat replies normally.');return
    }
    // A single addressee removes the wake word: while the panel is in chat
    // mode, every plain utterance is a conversation turn — the dictation draft
    // must NOT also hear it. The follow-up window gives the same routing for a
    // few seconds after a spoken reply in any mode. Only plain speech takes
    // this route — a wake-word command keeps its normal meaning, which is what
    // lets "Mux, stop" still kill playback mid-dialog.
    if(parsed.command===null&&chatAddressee()&&onAssistantRef.current){
      const wakeIntent=extractWakeIntent(rawText,statusRef.current?.wake_words?.length?statusRef.current.wake_words:DEFAULT_WAKE_WORDS)
      if(wakeIntent===null&&rawText.trim()){
        // A bare "hold on" enters the brainstorm hold without the wake word;
        // exact-match only, so a sentence merely containing it is never eaten.
        if(matchesBarePhrase(rawText,HOLD_ENTER_PHRASES)){beginHold();return}
        // The pen decides whether this is a turn or half of one. A held fragment
        // merges and dispatches whatever the merged text looks like - the
        // heuristic deliberately does not run again, which is what makes "one
        // deferral per utterance" bounded rather than a rule to remember.
        const routed=penRef.current.offer(rawText,configuredPatience())
        if(routed.kind==='defer'){armDeferral(routed.deferred);return}
        if(routed.merged){clearDeferralTimer();reportDeferral(routed.merged,'merged')}
        try{
          if(await askAssistant(routed.text))return
        }catch(cause){reportFailure(cause);return}
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
      const dropped=resolveDeferral('discarded')
      setProvisional('')
      setDraft(clearDraft());setPhase('listening')
      respond(dropped?'Voice message cleared, and the unfinished sentence was dropped. Still listening.':'Voice message cleared. Still listening.');return
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
        // The whole reply: a clip answered from the store arrives with every
        // segment it already had, and a fresh one continues on its claimed stream.
        await playClipGroup(clip,session.id,'agent');setPhase('listening');respond('Reply playback started. Still listening.')
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
    // Counted around the whole decode-and-act path so a held fragment's release
    // timer can tell "the room went quiet" from "the continuation is in flight".
    decodingRef.current++
    try{await transcribeInner(audio,marks)}
    finally{decodingRef.current=Math.max(0,decodingRef.current-1)}
  }

  const transcribeInner=async(audio:Blob,marks:CaptureMarks)=>{
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
      // The accurate transcript is here, so the provisional reading of the same
      // breath has done its job. Cleared BEFORE the pen sees the text: whatever
      // the pen holds becomes the row's content, and leaving the speculative
      // copy up would show the same sentence twice, once badly.
      settleProvisional(marks.utteranceId)
      recordHistory('you',decoded.text)
      // The echo policy applies to *unconfirmed* overlap only, and the rule now
      // lives in `playbackTranscriptVerdict` so it can be asserted rather than
      // read: refusing a *confirmed* barge-in is how a full spoken sentence came
      // to be transcribed and then answered with "Playback command ignored"
      // (measured 2026-08-23: 2,688 ms of audio, 35 characters decoded, no turn).
      const spoken=marks.playbackOriginAtStart==='system'
        ?extractWakeIntent(decoded.text,statusRef.current?.wake_words?.length?statusRef.current.wake_words:DEFAULT_WAKE_WORDS)
        :null
      const verdict=playbackTranscriptVerdict({
        playbackAtStart:marks.playbackAtStart,
        bargeInConfirmed:marks.bargeInConfirmed,
        playbackOriginAtStart:marks.playbackOriginAtStart,
        isPlaybackControl:isPlaybackControl(decoded.parsed.command),
        wakeIntent:spoken,
        wakeIntentIsSafeQuery:spoken!==null&&safeDuringSystemPlayback(spoken),
      })
      if(verdict.action==='deliver')await handleTranscript(decoded.parsed,decoded.text)
      else if(verdict.action==='deliver-query'){
        bargeInPlayback()
        await handleTranscript({command:null,text:''},decoded.text)
      }else{
        setPhase('listening')
        respond(verdict.reason==='agent-echo'
          ?`Playback echo ignored. Say “${wakeRef.current}, mute” before another command.`
          :verdict.reason==='system-unaddressed'
            ?`Playback command ignored. Say “${wakeRef.current}” before a read-only lookup or navigation command.`
            :'Playback command ignored. During application speech, only read-only lookup and navigation commands are allowed.')
      }
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
    // A bare confirm/cancel over an open card is the same kind of evidence a
    // wake-worded command is: a closed, complete utterance whose meaning cannot
    // change by continuing. Waiting the rest of the tail for it makes answering
    // a question the slowest thing the operator does, and every second spent
    // there is a second a scheduled action is running down its cancel window.
    const verdict=awaitingVerdict()?spokenConfirmation(decoded.text):null
    if(!decoded.parsed.command&&!verdict){
      // Not a command, so this decode does NOT end the utterance - the real one
      // still has to run on the dictation profile, and the endpoint still has to
      // prove the operator is finished. What changes is that their words stop
      // being invisible for the several seconds that takes. Refused once the
      // accurate transcript has landed, so a late speculation cannot overwrite
      // it, and only while the assistant is the addressee: in talk mode the
      // dictation draft already shows the text.
      if(settledUtteranceRef.current!==marks.utteranceId&&chatAddressee()&&!standbyRef.current){
        const text=decoded.text.trim()
        if(text)setProvisional(text)
      }
      return
    }
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
    // Every refusal below goes through `reportFailure`, not a bare `setDetail`.
    // That is not cosmetic: `respond` is what writes the sentence into Talk
    // history, and history is persisted. A refusal that only set the phase left
    // `talk:error` on screen and *nothing at all* on disk, which is why this
    // failure survived so long - it produced no request for the access log, no
    // history entry, and a chip with three words in it.
    if(!capability.available){reportFailure(capability.reason);return}
    // Ask before refusing. `status` is fetched once when the app mounts, so a
    // page that lost that one fetch used to answer every press for the rest of
    // its life with "Daemon transcription is unavailable" - a claim about a
    // daemon it had never successfully asked. The desktop shell is where that
    // bites, because its page is opened once and kept for days across daemon
    // restarts, while a browser tab gets reloaded and quietly repairs itself.
    let voice=statusRef.current
    if(!voice&&refreshStatusRef.current){
      setPhase('starting');setDetail('Reading the voice status from the daemon…')
      voice=await refreshStatusRef.current()
    }
    if(!voice){
      reportFailure('swe-mux has not been able to read the voice status from the daemon. '
        +'Check that the daemon is running, then press Talk again.')
      return
    }
    if(!voice.stt_available){reportFailure(voice.stt_diagnostic||'Daemon transcription is unavailable.');return}
    setPhase('starting');setDetail('Requesting microphone…')
    // Unlock only: this is the gesture mobile browsers require before any later
    // programmatic play(), so the `read reply` command works. It turns nothing on.
    unlockPlayback()
    const capture=new PersistentVoiceCapture({
      playbackActive:()=>getPlayback().playing,
      playbackOrigin:()=>getPlayback().origin,
      onPlaybackProbe:active=>setPlaybackDucked(active),
      onPlaybackProbeResult:result=>{void api('POST','/api/voice/barge-in-diagnostic',result,{timeoutMs:4000}).catch(()=>{})},
      // The watchdog's whole reason to exist (2026-08-20): the phone's capture died
      // while the UI said "listening", and no transcribe request ever left the
      // device again. The stall is reported to the daemon FIRST — durable evidence
      // that survives the phone being walked away from — then to the operator.
      onCaptureStall:diagnostic=>{
        void api('POST','/api/voice/capture-diagnostic',diagnostic,{timeoutMs:4000}).catch(()=>{})
        if(!enabledRef.current)return
        setPhase('stalled')
        setDetail(diagnostic.trackState==='ended'?STALL_ENDED_DETAIL:STALL_DETAIL)
        recordHistory('mux','Microphone stalled: the device stopped delivering audio.')
      },
      onCaptureRecovered:diagnostic=>{
        void api('POST','/api/voice/capture-diagnostic',diagnostic,{timeoutMs:4000}).catch(()=>{})
        if(!enabledRef.current)return
        setPhase(current=>current==='stalled'?(standbyRef.current?'standby':'listening'):current)
        setDetail(current=>current===STALL_DETAIL||current===STALL_ENDED_DETAIL?'Microphone recovered. Listening…':current)
        recordHistory('mux','Microphone recovered.')
      },
      onSpeechStart:()=>{speakingRef.current=true;if(!enabledRef.current)return;if(standbyRef.current){setPhase('standby');return}setPhase('hearing');setDetail(getPlayback().playing?'Listening through playback…':'Listening…')},
      onBargeIn:()=>{bargeInPlayback();if(enabledRef.current&&!standbyRef.current){setPhase('hearing');setDetail('Playback stopped. Listening…')}},
      // Fired at the endpoint, before any text exists. It is the whole point of the
      // phase: the answer has to arrive when the user stops talking, not when the
      // decode does, or the pause reads as a failure.
      onSpeechEnd:()=>{speakingRef.current=false;if(!enabledRef.current||standbyRef.current)return;playEarcon('heard');setPhase('heard');setDetail('Heard you.')},
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
      // Chat patience: while the assistant is the microphone's addressee, plain
      // speech gets a longer trailing-silence window before it becomes a turn —
      // thinking out loud is not answered at every pause. Commands stay fast:
      // the speculative decode short-circuits the tail for wake-worded phrases.
      endpointPatienceMs:()=>{
        if(standbyRef.current||!chatAddressee())return 0
        // Patience is for thinking out loud. An open confirmation card means the
        // assistant asked a closed question, so the answer gets the detector's
        // ordinary tail — holding a one-word reply for another 1.2 s is the
        // difference between the confirmation feeling instant and feeling stuck.
        if(awaitingVerdict())return 0
        // The adaptive half of the deferral: while an unfinished utterance is
        // held, the continuation gets a roomier tail so the second breath is not
        // itself chopped in half. It ends when the deferral resolves, and a
        // deferral resolves exactly once, so the extension cannot compound.
        //
        // The exact window the pen granted, so weak evidence buys a short tail
        // and strong evidence a long one. A park grants no window - it is waiting
        // for speech rather than for silence - and correctly falls back to the
        // ordinary patience here.
        return endpointPatienceMs(configuredPatience(),penRef.current.pending?.extensionMs??null)
      },
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
    }catch(cause){
      capture.stop();if(startingRef.current!==capture)return
      startingRef.current=null;enabledRef.current=false;setActive(false)
      // Only here, and not in every `reportFailure`: the WebView2 note answers
      // "was the microphone refused, and by whom", which is the question a
      // failed `capture.start()` raises and no other failure does.
      reportFailure(captureFailureNote(cause instanceof Error?cause.message:String(cause),desktopMediaReport()))
    }
  }

  // Composed here rather than in the panel: the three states that feed it are
  // all owned by this hook, and the ordering rule ("what is already held, then
  // what is being said") is a correctness claim with its own unit tests.
  //
  // This reads a REF during render, which repaints only because every mutation
  // of the pen is already paired with a state set — `armDeferral` and
  // `parkAssistantHold` set the trigger and the phase, and every path that
  // empties the pen goes through `clearDeferralTimer`, which clears them. A
  // future pen mutation that skips that pairing would leave this row showing
  // stale words with nothing to say it had gone wrong, so pair it or give the
  // pen its own state.
  const pending=pendingUtterance({hold,holdBuffer,held:penRef.current.pending,provisional})

  return {
    target,targetAvailable,pinned:!!pinnedTarget,
    // Derived rather than stored: readiness has one source of truth (`detector`), and
    // an idle phase that claimed `listening` before the detector loaded would be true
    // and still misleading — capture is listening, on the fallback's 900 ms tail.
    phase:phase==='listening'&&detector===null?'warming':phase,
    detail,draft:draft.text,active,standby,hold,holdBuffer,deferredTrigger,deferredSource,
    // Recomposed on every render rather than stored: it is a view of three
    // pieces of state that already exist, and a fourth copy of them is a fourth
    // thing to keep in sync.
    pendingText:pending.text,pendingNote:pending.note,
    comms:!!commsTargetId,wake,landedAt,latency,detector,history,
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
    releaseHold:()=>{void releaseHold()},
    discardHold,
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

/** How long a finger has to stay down before the mic reads it as "start capture". */
const CAPTURE_HOLD_MS=550
/** How far it may drift first; past this the gesture is a scroll, not a hold. */
const CAPTURE_HOLD_SLOP_PX=10

/**
 * The one voice control in the top bar: click opens the panel, ctrl+click listens.
 *
 * It used to be two buttons - a microphone and a panel chip beside it - on the theory
 * that a control should do one thing. In use that read as two voice buttons whose
 * difference had to be remembered, on the row with the least space in the app. So the
 * chip is gone and this is both, with the two jobs separated by the modifier rather than
 * by the button:
 *
 * - **Plain click toggles the panel** and never touches capture. That is the common
 *   action, so it is the unmodified one.
 * - **Ctrl/Cmd+click toggles capture**, as does a long press, which is what a phone has
 *   instead of a modifier (the same 550 ms hold the sidebar and rail use). The panel's
 *   own mic button is the primary capture control on touch; this is the shortcut.
 * - **The colour means one thing: capture is live.** Opening or closing the panel never
 *   changes it. A control that lit up for two different reasons could not answer the one
 *   question a microphone has to answer - "is this listening to me right now?" - which is
 *   why the lit state is bound to `conversation.phase` and to nothing else. Off is the
 *   same recessed treatment as the unconfigured state, because both mean "not listening";
 *   the slashed glyph, `aria-pressed`, and the label carry the difference.
 *
 * It also carries what is waiting behind a collapsed panel - a count while confirmation
 * cards are open, a dot when a reply landed - because it is now the only way back to a
 * dock that is streaming, speaking, and opening cards from the chip.
 */
export function VoiceControl({
  conversation,configured,dock,pendingActions,unseen,onToggleDock,
}:{
  conversation:Conversation
  /** `stt_enabled`: without it there is a panel to open but no capture to start. */
  configured:boolean
  dock:VoiceDockState
  /** Open confirmation cards. They expire, so they outrank an unread reply. */
  pendingActions:number
  /** A reply landed while the dock was collapsed. */
  unseen:boolean
  onToggleDock:()=>void
}){
  const active=conversation.phase!=='off'
  const collapsed=dock==='chip'
  const holdRef=useRef<{timer:number;x:number;y:number}|null>(null)
  // A hold that fired must not also open the panel when the finger lifts: the click
  // follows the pointer sequence, so it is swallowed once rather than prevented.
  const heldRef=useRef(false)
  const toggleCapture=()=>{
    if(!configured){requestSetting('voice.stt');return}
    conversation.toggle()
  }
  const clearHold=()=>{
    if(holdRef.current)clearTimeout(holdRef.current.timer)
    holdRef.current=null
  }
  const beginHold=(event:JSX.TargetedPointerEvent<HTMLElement>)=>{
    heldRef.current=false
    if(event.pointerType==='mouse')return
    const x=event.clientX,y=event.clientY
    holdRef.current={x,y,timer:window.setTimeout(()=>{
      holdRef.current=null
      heldRef.current=true
      toggleCapture()
    },CAPTURE_HOLD_MS)}
  }
  const moveHold=(event:JSX.TargetedPointerEvent<HTMLElement>)=>{
    const hold=holdRef.current
    if(!hold)return
    if(Math.abs(event.clientX-hold.x)>CAPTURE_HOLD_SLOP_PX||Math.abs(event.clientY-hold.y)>CAPTURE_HOLD_SLOP_PX)clearHold()
  }
  const captureLabel=!configured
    ? 'Set up hands-free conversation'
    : active?'Stop listening':'Start listening'
  const detail=pendingActions
    ?`${pendingActions} confirmation${pendingActions===1?'':'s'} waiting`
    :unseen
      ?'a reply you have not read'
      :active?'the microphone is live':'nothing waiting'
  return <button
    class={`conversation-talk-toggle ${active?'active':'off'}${configured?'':' setup'}${collapsed?'':' dock-open'}${pendingActions?' pending':''}`}
    aria-label={collapsed?'Open the voice panel':'Collapse the voice panel to the top bar'}
    aria-expanded={!collapsed}
    // Pressed is capture, matching the colour. The panel's state is `aria-expanded`,
    // so the two are never confused by a screen reader either.
    aria-pressed={configured?active:undefined}
    title={`${collapsed?'Open':'Collapse'} the voice panel - ${detail}.\nCtrl+click (or hold) to ${captureLabel.toLowerCase()}.${
      conversation.target?`\nTarget: ${conversation.target.label}`:''}`}
    onPointerDown={beginHold}
    onPointerMove={moveHold}
    onPointerUp={clearHold}
    onPointerCancel={()=>{clearHold();heldRef.current=false}}
    onClick={event=>{
      if(heldRef.current){heldRef.current=false;return}
      if(event.ctrlKey||event.metaKey){toggleCapture();return}
      onToggleDock()
    }}
  >
    <MicIcon slashed={!active}/>
    {pendingActions>0
      ?<i class="voice-dock-badge">{pendingActions>9?'9+':pendingActions}</i>
      :unseen?<i class="voice-dock-dot" aria-hidden="true"/>:null}
  </button>
}

/**
 * The one voice surface: a floating row under the top bar carrying the dictation draft
 * and the assistant conversation, drawn at whichever size the dock is set to.
 *
 * Three things are kept apart here on purpose, having previously been one:
 *
 * - **Capture** is `conversation.phase`. Its primary control is the mic button in this
 *   header; the top-bar control reaches the same toggle behind ctrl+click or a hold.
 *   Opening or closing the dock never starts or stops it. Before the split, this surface
 *   existed only while capture ran, so the only way to clear it off the workspace was to
 *   stop the microphone.
 * - **The body** is `mode`, the talk/chat/tts tabs: the dictation draft, the assistant, or
 *   read aloud's operational panel. It decides what is drawn, and - for the two
 *   conversational bodies - who plain speech reaches. It says nothing about how much of
 *   the dock is on screen.
 * - **Size** is `dock` (`voiceDock.ts`): full, a one-row peek, or collapsed to the top bar.
 *
 * It floats rather than taking a workspace track for the same reason the read-aloud strip
 * does: a pane whose row count changes with a voice toggle resizes the PTY under a live
 * agent, and the reflowed scrollback does not come back when the panel closes.
 *
 * The section always renders, at every dock state, and both bodies are always in the tree.
 * `assistantView` is mounted once by App and must never be unmounted by a size or mode
 * change - the announced-card set that stops a scheduled card being spoken twice lives
 * inside it (`AssistantPanel.tsx`).
 */
export function VoiceDock({
  conversation,commands,configuredCommands,onOpenSettings,mode,onMode,assistantView,readView,
  captureConfigured,dock,onDock,
}:{
  conversation:Conversation
  commands:Command[]
  configuredCommands?:{action:string;phrases:string[]}[]
  onOpenSettings:()=>void
  mode:VoicePanelMode
  onMode:(mode:VoicePanelMode)=>void
  /** The assistant conversation view, mounted once by App. */
  assistantView:ComponentChildren
  /** The read-aloud panel, mounted once by App for the same reason. */
  readView:ComponentChildren
  /** `stt_enabled`. Without it the panel still works; only capture is unavailable. */
  captureConfigured:boolean
  dock:VoiceDockState
  onDock:(step:'expand'|'collapse')=>void
}){
  const draftRef=useRef<HTMLTextAreaElement|null>(null)
  const historyRef=useRef<HTMLDivElement|null>(null)
  const [landed,setLanded]=useState(false)
  const [historyOpen,setHistoryOpen]=useState(()=>loadVoiceConversationHistoryOpen())
  const talkActive=conversation.phase!=='off'
  const effectiveMode=effectiveVoicePanelMode(mode,talkActive)
  const chat=effectiveMode==='chat'
  const read=effectiveMode==='read'
  // The draft's controls belong to the draft. `!chat` meant that while there were
  // two tabs; with a third it would put send/undo/comms on the read-aloud panel.
  const dictating=effectiveMode==='dictation'
  const talkVariant=voiceBodyVariant(dock,effectiveMode,'dictation')
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
  //
  // `talkVariant` is a dependency because the textarea only exists in the full body:
  // reopening the dock mounts a fresh, unsized one, and without a re-run it would sit
  // at one line under a five-line draft.
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
    // Grow to fit, bounded by the min/max in CSS. Affordable only because the dock floats
    // — measuring requires collapsing to `auto` first, and `scrollHeight` excludes the
    // border that `box-sizing:border-box` makes the inline height responsible for.
    element.style.height='auto'
    element.style.height=`${element.scrollHeight+element.offsetHeight-element.clientHeight}px`
  },[conversation.draft,talkVariant])
  useLayoutEffect(()=>{
    if(historyOpen&&historyRef.current)historyRef.current.scrollTop=historyRef.current.scrollHeight
  },[conversation.history.length,historyOpen,talkVariant])
  const toggleHistory=()=>setHistoryOpen(value=>{
    const next=!value
    saveVoiceConversationHistoryOpen(next)
    return next
  })
  const send=()=>conversation.send()
  const draftPreview=conversation.draft.trim().replace(/\s+/g,' ')
  const phaseHint=!dictating
    ? `${conversation.detail?conversation.detail+' · ':''}The dictation draft is not on screen, so the microphone addresses the assistant: plain speech becomes a conversation turn and never lands in the draft. Wake-word commands keep their normal meaning.`
    : `${conversation.detail}${conversation.detail?' · ':''}${conversation.detector===null
      ? 'The speech detector is still loading. Capture is already listening, but on the fallback detector, so an utterance needs a longer pause to end. Hold Alt+Shift+Space to talk with no pause detection at all.'
      : conversation.detector==='silero'
        ? 'Silero voice activity detection: an utterance ends after a short pause. Hold Alt+Shift+Space to talk with no pause detection at all.'
        : 'Energy-based detection: an utterance needs a longer pause to end. Hold Alt+Shift+Space to talk with no pause detection at all.'}`
  return <section
    class={`voice-dock ${dock} ${read?'read-mode':chat?'chat-mode':'talk-mode'} ${conversation.phase}${landed?' landed':''}`}
    aria-label="Voice panel"
    aria-hidden={dock==='chip'?'true':undefined}
  >
    {/* The header carries a class of its own because it is a gesture region
        (`mobileGestures.ts`): swiping it works the panel — up/down between its three
        sizes, left/right across its three modes. The header and not the whole dock,
        because every body below it scrolls. */}
    <header class="voice-dock-head">
      {/* Two steps rather than one cycling button, so neither direction is a guess and
          each can be disabled at its end. The chip in the top bar is the third stop and
          the way back from it. */}
      <div class="voice-dock-size" role="group" aria-label="Voice panel size">
        <button
          class="voice-dock-step"
          aria-label="Collapse the voice panel one step"
          title={dock==='peek'?'Collapse to the top bar — the conversation and the microphone keep running':'Collapse to a single row'}
          disabled={!canCollapseVoiceDock(dock)}
          onClick={()=>onDock('collapse')}
        >▴</button>
        <button
          class="voice-dock-step"
          aria-label="Expand the voice panel one step"
          title={dock==='peek'?'Expand to the full conversation':'Expand'}
          disabled={!canExpandVoiceDock(dock)}
          onClick={()=>onDock('expand')}
        >▾</button>
      </div>
      {/* The panel's own microphone, and the primary capture control: on touch there
          is no ctrl+click, so the top-bar mic's hold is a shortcut and this is the
          button. It carries the same one meaning as that one - lit means listening -
          and it is the only thing in this header that touches capture. */}
      <button
        class={`voice-dock-mic ${talkActive?'active':'off'}${captureConfigured?'':' setup'}`}
        aria-label={captureConfigured?(talkActive?'Stop listening':'Start listening'):'Set up hands-free conversation'}
        aria-pressed={captureConfigured?talkActive:undefined}
        title={captureConfigured
          ?(talkActive?'Stop listening and release the microphone':'Start listening')
          :'Microphone conversation is disabled - open its switch'}
        onClick={captureConfigured?()=>conversation.toggle():()=>requestSetting('voice.stt')}
      ><MicIcon slashed={!talkActive}/></button>
      <div class="voice-mode-toggle" role="tablist" aria-label="Voice panel mode">
        <button role="tab" aria-selected={effectiveMode==='dictation'} class={effectiveMode==='dictation'?'active':''} disabled={!talkActive} title={talkActive?'Dictation draft and Talk history':'Start listening to dictate'} onClick={()=>onMode('dictation')}>talk</button>
        <button role="tab" aria-selected={chat} class={chat?'active':''} title="Converse with the Mux assistant" onClick={()=>onMode('chat')}>chat</button>
        <button role="tab" aria-selected={read} class={read?'active':''} title="Read aloud: what speaks, for which session, and every clip waiting" onClick={()=>onMode('read')}>tts</button>
      </div>
      {talkActive&&<span class={`dictation-phase ${conversation.phase}`} title={phaseHint}>talk:{conversation.phase==='transcribing'?'typing':conversation.phase}</span>}
      {/* Stated, never silent: any body but the draft leaves the assistant as the
          addressee, so the header says where speech is going. */}
      {talkActive&&!dictating&&<span class="dictation-mic-note" title="The dictation draft is not on screen, so plain speech goes to the assistant">mic→assistant</span>}
      {talkActive&&conversation.hold&&<span class="dictation-phase standby" title="Brainstorm hold: speech keeps transcribing into a buffer and the assistant answers only when you say “go ahead”. “Mux, cancel” clears the buffer.">holding{conversation.holdBuffer?` · ${conversation.holdBuffer.trim().split(/\s+/).length}w`:''}</span>}
      {/* Two different holds, and the difference matters to the operator: the
          heuristic's expires into an ordinary turn, so waiting is enough; the
          assistant's never sends on its own, so only more speech resolves it. */}
      {talkActive&&!conversation.hold&&!!conversation.deferredTrigger&&conversation.deferredSource==='assistant'&&<span class="dictation-phase standby" title="The assistant read that as half a thought with nothing to answer yet, so it stayed silent and kept your words. Finish the sentence and both halves go as one turn; “Mux, cancel” drops it.">unfinished · waiting for the rest</span>}
      {talkActive&&!conversation.hold&&!!conversation.deferredTrigger&&conversation.deferredSource!=='assistant'&&<span class="dictation-phase standby" title={`That sentence ended on “${conversation.deferredTrigger}”, so the turn is held for one extra pause instead of being answered mid-clause. Keep talking and the two halves go together; stay quiet and it sends as-is.`}>unfinished · “{conversation.deferredTrigger}”</span>}
      {/* The failure itself, on screen. `talk:error` names a phase and says
          nothing about what went wrong; the sentence that does was reachable
          only from the chip's tooltip and this live region, so an operator who
          did not think to hover got three words and no lead. It truncates
          rather than wrapping - the header is one row - and carries the whole
          text in its title. */}
      {conversation.phase==='error'&&!!conversation.detail&&
        <span class="dictation-failure" role="alert" title={conversation.detail}>{conversation.detail}</span>}
      <span class="sr-only" role="status" aria-live="polite">{conversation.detail}</span>
      {dictating&&conversation.latency&&<span class="dictation-latency" title={`End of speech to action — ${formatLatency(conversation.latency)}`}>{Math.round(conversation.latency.total_ms)} ms</span>}
      <div class="dictation-actions">
        {dictating&&<button class="dictation-send" title="Commit the draft to the named target (Ctrl+Enter)" disabled={!conversation.draft.trim()||!conversation.targetAvailable} onClick={send}>send</button>}
        {dictating&&<button title="Append the draft to the target without submitting" disabled={!conversation.draft.trim()||!conversation.targetAvailable} onClick={()=>conversation.append()}>append</button>}
        {dictating&&<button title="Remove the last phrase that was heard" disabled={!conversation.draft} onClick={()=>conversation.undo()}>undo</button>}
        {dictating&&<button title="Clear the draft" disabled={!conversation.draft} onClick={()=>conversation.clear()}>clear</button>}
        {dictating&&talkActive&&<button class={conversation.standby?'active':''} title={conversation.standby?'Resume listening':'Keep the mic open but ignore speech until resumed'} onClick={()=>conversation.toggleStandby()}>{conversation.standby?'resume':'standby'}</button>}
        {dictating&&<button class={conversation.comms?'active':''} aria-pressed={conversation.comms} title={conversation.comms?'Exit short spoken agent replies and restore prior read-aloud settings':'Pin this agent and request short spoken replies'} onClick={()=>conversation.toggleComms()}>comms:{conversation.comms?'on':'off'}</button>}
        {chat&&talkActive&&conversation.hold&&<button class="dictation-stop" title="Send the buffered brainstorm to the assistant now" onClick={()=>conversation.releaseHold()}>go ahead</button>}
        {chat&&talkActive&&conversation.hold&&<button class="dictation-stop" title="Discard the buffered brainstorm and leave hold" onClick={()=>conversation.discardHold()}>discard</button>}
        <VoiceCommandsButton commands={commands} configuredCommands={configuredCommands}/>
        <button class="dictation-settings" aria-label="Open Voice settings" title="Open Voice settings (engine, wake words, commands)" onClick={onOpenSettings}>⚙</button>
      </div>
    </header>
    {/* Both bodies are always in the tree; `hidden` decides which one is drawn. */}
    {/* The talk body may be dropped when it is not the addressee — unlike the assistant
        view below it, everything it renders is a view of `conversation`, which lives in
        the app-level controller and outlives it. Keeping a hidden textarea mounted would
        also leave the composer in the accessibility tree at the peek. */}
    <div class="voice-dock-body voice-dock-talk" hidden={talkVariant==='hidden'}>
      {talkVariant==='hidden'
        ?null
        :talkVariant==='peek'
        ?<p class="voice-dock-line" title={draftPreview||'The dictation draft is empty.'}>
          <b>to</b>
          <em class={conversation.targetAvailable?'':'unavailable'}>{conversation.target?.label||'no target'}</em>
          <span>{draftPreview||'draft empty'}</span>
        </p>
        :<>
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
        </>}
    </div>
    <div class="voice-dock-body voice-dock-chat" hidden={chat?undefined:true}>{assistantView}</div>
    {/* Hidden rather than dropped, for the same reason as the assistant body: the panel
        subscribes to clip events and holds the list it has already fetched, and a
        remount would re-request every clip on each tab switch. */}
    <div class="voice-dock-body voice-dock-read" hidden={read?undefined:true}>{readView}</div>
  </section>
}
