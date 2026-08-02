import { useEffect, useRef, useState } from 'preact/hooks'
import { memo } from 'preact/compat'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { SearchAddon } from '@xterm/addon-search'
import { WebLinksAddon } from '@xterm/addon-web-links'
import { WebglAddon } from '@xterm/addon-webgl'
import { Unicode11Addon } from '@xterm/addon-unicode11'
import { ClipboardAddon } from '@xterm/addon-clipboard'
import '@xterm/xterm/css/xterm.css'
import { api, openWebSocket, uploadTerminalImage } from './api'
import type { Session } from './types'
import { keyChord } from './keys'
import { resolvedTheme, terminalThemes, type ThemeName } from './theme'
import { AGENT_NEWLINE, terminalKeyDecision } from './terminalKeys'
import { isTerminalProtocolResponse, shouldSuppressTerminalProtocolResponse } from './terminalProtocol'
import { clampContextMenuLeft, fitMenuInViewport } from './menuPosition'
import {
  mobileDragTarget,
  terminalCellAtPoint,
  terminalScrollLines,
  terminalSelectionSpan,
  terminalWordRange,
  touchWheelDelta,
  type MobileInputSettings,
  type TerminalCell,
} from './mobileInput'
import { isMobileTerminalInput, mobileImeDelta } from './mobileTerminalIme'
import { clipboardImage, copyPreparedText, hasTerminalImage, isTerminalImage, pasteNeedsManualBracketing, ResilientClipboardProvider } from './terminalClipboard'
import { noteTerminalFocus } from './insertTarget'
import { captureCopy } from './clipboardHistory'
import { resumeCommand } from './resumeCommand'
import { railPayload, resolveRail, type RailBackend, type RailItem } from './commandRail'
import { activatePromptRailItem } from './promptRail'
import { BranchIcon, CopyIcon, PasteIcon } from './railIcons'
import { currentProfile, loadRailItems } from './deviceSettings'
import { APP_TAIL_KEY, VIEWPORT_SETTLE_MS, appOwnsTail, attachRegistersViewport, createViewportScheduler, redrawVisibleTerminal, refitVisibleTerminal, scrollTerminalToTail, terminalHostIsVisible } from './terminalViewport'
import { geometryMatchesFit, letterboxFontSize } from './terminalLetterbox'
import {
  applyOwnerFrame,
  applyOwnerReleased,
  applyRejectedFrame,
  claimReasonForFocus,
  inputOwnerNotice,
  shouldReclaimAfterDisplacement,
  shouldReplayRejectedInput,
  UNOWNED,
  type ClaimReason,
  type OwnershipView,
} from './inputOwnership'
import { pendingInputDecision } from './pendingInput'
import { pastePayload } from './noteSelection'
import { deviceIsFocused, PRESENCE_REPORTED_EVENT } from './devicePresence'
import { localPreviewUrl } from './previewLinks'
import { HANDSHAKE_TIMEOUT_MS, retryDelay, watchLiveness, type ConnectionPhase } from './liveness'
import {
  shouldLoadWebgl,
  terminalAttachReadyFrame,
  type ActiveTerminalRenderer,
  type TerminalRendererPreference,
  type WindowsPtyCompatibility,
} from './terminalRenderer'
import {
  isWebglRenderError,
  recordTerminalRenderDiagnostic,
  terminalRenderDiagnosticsEnabled,
} from './terminalRenderDiagnostics'

type StartupMilestone = 'pane_mounted' | 'socket_open' | 'replay_ready'

/** The pane's normal font size. A letterboxed pane renders below it and never above. */
const BASE_FONT_SIZE = 11

/**
 * How long a letterbox must persist before the pane says so.
 *
 * Every ordinary resize letterboxes for a moment: the fit pass re-measures and compares
 * against a `serverGeometry` that is by definition one round trip stale, so the pane
 * draws the old grid until the daemon's `geometry` frame lands. Announcing that would be
 * a chip flickering on every drag. What is worth announcing is the state that does not
 * resolve, which is the bug this notice exists for.
 */
const LETTERBOX_NOTICE_DELAY_MS = 1500

interface Props {
  session: Session
  onState: (session: Session) => void
  onStartupTiming?: (milestone: StartupMilestone, elapsedMs: number) => void
  startupOrigin?: number
  broadcast: boolean
  keybindings: Record<string, string>
  scrollback: number
  rendererPreference: TerminalRendererPreference
  /** ConPTY compatibility descriptor from the daemon; undefined off Windows. */
  windowsPty?: WindowsPtyCompatibility
  mobileInput: MobileInputSettings
  /**
   * Whether this pane is the one on screen in its stack. A false value is a *warm*
   * pane (`warmPanes.ts`): fully live, deliberately kept mounted so returning to it
   * costs no replay, but not being looked at. It therefore has to behave like a
   * minimized window rather than like a visible one — it deregisters its viewport so
   * it cannot reshape the shared PTY, and it never writes the system clipboard.
   *
   * Read through a ref inside the mount effect rather than being one of its
   * dependencies: making it a dependency would dispose and rebuild the terminal on
   * every tab switch, which is the exact cost warm panes exist to remove.
   */
  visible: boolean
  /** Open the command-rail settings editor (the rail's trailing gear). */
  onConfigureRail?: () => void
  /** Fork this agent conversation into a sibling pane (rail Branch button). */
  onBranch?: () => void
}

function runCommand(command: string) {
  window.dispatchEvent(new CustomEvent('mux:command', { detail: command }))
}

function reportError(message: string) {
  window.dispatchEvent(new CustomEvent('mux:error', { detail: message }))
}

function acceptsClipboardImages(session: Session) {
  return session.backend === 'claude' || session.backend === 'codex'
}

/**
 * Paste text, wrapping it by hand when xterm's mirror of the child's bracketed-paste
 * mode has gone stale.
 *
 * xterm only wraps a paste once it has *seen* the child enable the mode, and an agent
 * CLI enables it exactly once at startup. A pane that reset its terminal on reconnect
 * can therefore come back believing the mode is off while the CLI still has it on.
 * Unwrapped, xterm rewrites every newline to a carriage return, so the CLI submits the
 * paste one line at a time and keeps only the text after the final newline.
 *
 * The daemon restates the mode in its replay, which fixes this at the source; this is
 * the backstop for a session so long-lived that even retained scrollback has rolled
 * past the enable. Restricted to multi-line text into an agent session: that is the
 * only shape this bug can affect, so nothing else changes behaviour.
 */
function pasteIntoTerminal(term: Terminal, session: Session, text: string) {
  if (pasteNeedsManualBracketing({
    text,
    agentBackend: acceptsClipboardImages(session),
    bracketedPasteMode: term.modes.bracketedPasteMode,
  })) {
    // term.input keeps this on the normal onData path, so broadcast membership and
    // the replay guard treat it exactly like a real paste.
    term.input(pastePayload(text), true)
    return
  }
  term.paste(text)
}

function mobileClipboardFallback(): boolean {
  return window.matchMedia('(max-width: 760px), (pointer: coarse)').matches
}

async function insertTerminalImage(term: Terminal, session: Session, blob: Blob) {
  if (!acceptsClipboardImages(session)) throw new Error('Images can be attached only to Claude or Codex sessions.')
  if (!isTerminalImage(blob)) throw new Error('Supported image types are PNG, JPEG, WebP, and GIF.')
  if (session.backend === 'codex' && !term.modes.bracketedPasteMode) {
    throw new Error('Codex is not ready for an image yet. Wait for its chat prompt and try again.')
  }
  const form = new FormData()
  form.append('file', blob, `clipboard.${blob.type.split('/')[1] || 'png'}`)
  const result = await uploadTerminalImage<{reference:string}>(`/api/sessions/${session.id}/media`, form)
  // Claude and Codex both recognize one isolated bracketed paste containing a readable
  // image path. Codex turns it into LocalImage rather than leaving the path as draft text.
  term.paste(result.reference)
  term.focus()
}

async function pasteBrowserClipboard(term: Terminal, session: Session) {
  if (acceptsClipboardImages(session) && typeof navigator.clipboard.read === 'function') {
    try {
      const items = await navigator.clipboard.read()
      const item = items.find(candidate => candidate.types.some(type => type.startsWith('image/')))
      const mediaType = item?.types.find(type => type.startsWith('image/'))
      if (item && mediaType) {
        await insertTerminalImage(term, session, await item.getType(mediaType))
        return
      }
    } catch {
      // Some browsers block the richer Clipboard API while still allowing text reads.
    }
  }
  pasteIntoTerminal(term, session, await navigator.clipboard.readText())
  term.focus()
}

function TerminalPaneImpl({ session, onState, onStartupTiming, startupOrigin, broadcast, keybindings, scrollback, rendererPreference, windowsPty, mobileInput, visible, onConfigureRail, onBranch }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const searchRef = useRef<SearchAddon | null>(null)
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)
  const [findOpen, setFindOpen] = useState(false)
  const [findQuery, setFindQuery] = useState('')
  const [findCase, setFindCase] = useState(false)
  const [findResult, setFindResult] = useState<string>('')
  const [connectionState,setConnectionState]=useState<'connecting'|'connected'|'reconnecting'|'ended'>('connecting')
  const [preparedClipboard,setPreparedClipboard]=useState('')
  const [manualClipboard,setManualClipboard]=useState(false)
  const [selectionText,setSelectionText]=useState('')
  const [lastReply,setLastReply]=useState('')
  const [clipboardStatus,setClipboardStatus]=useState('')
  const [manualPaste,setManualPaste]=useState(false)
  const [imageDropActive,setImageDropActive]=useState(false)
  // True while the viewport sits above the newest line. Mirrored in a ref so the
  // per-render tail check can bail without touching component state.
  const [offTail,setOffTail]=useState(false)
  const offTailRef=useRef(false)
  // The same question about the *application's* viewport, which xterm cannot answer. A TUI
  // holding the mouse (Claude) receives a phone's drags as wheel events and scrolls itself,
  // so xterm's buffer never leaves its tail and `offTail` above stays false forever — which
  // is why the chip has only ever appeared in Codex sessions. Nothing reports that scroll
  // back to us, so the pane remembers having forwarded one, and the jump clears it.
  const [appOffTail,setAppOffTail]=useState(false)
  const appOffTailRef=useRef(false)
  // Which device may type into this session. Mirrored out of the mount effect so the
  // pane can say "input is on your phone" instead of silently swallowing keystrokes.
  const [inputOwnership,setInputOwnership]=useState<OwnershipView>(UNOWNED)
  const claimInputRef=useRef<(reason:ClaimReason)=>void>(()=>{})
  // Unlike an ordinary claim, the visible Resize/Take over action must measure and
  // register this pane before claiming it. A same-owner claim alone is a renewal and
  // the daemon deliberately performs no geometry work for one.
  const resizeToPaneRef=useRef<()=>void>(()=>{})
  // The grid this pane is rendering when it is not its own fit, as `cols×rows`, or ''
  // (see terminalLetterbox). The size and not just a flag, because when no *other
  // device* holds input there is no owner notice to hang it off, and a pane that
  // silently draws 80x24 into a 125x50 box reads as a broken UI rather than as a
  // session sized elsewhere. `letterboxSettled` below is what keeps that from flashing.
  const [letterboxSize,setLetterboxSize]=useState('')
  const letterboxActive=letterboxSize!==''
  const [letterboxSettled,setLetterboxSettled]=useState(false)
  useEffect(()=>{
    if(!letterboxSize){setLetterboxSettled(false);return}
    const timer=window.setTimeout(()=>setLetterboxSettled(true),LETTERBOX_NOTICE_DELAY_MS)
    return ()=>window.clearTimeout(timer)
  },[letterboxSize])
  // Bumped when another surface edits the shared rail config so this pane re-reads it.
  const [,bumpRailRev]=useState(0)
  useEffect(()=>{const on=()=>bumpRailRev(value=>value+1);window.addEventListener('mux:settings-changed',on);return()=>window.removeEventListener('mux:settings-changed',on)},[])
  // Ending a session is a two-click confirm, and App owns both the armed id and the
  // window that disarms it (`requestKill`). The rail button mirrors that broadcast
  // rather than running a second timer of its own, so its label can never disagree
  // with what the next click will actually do.
  const [killArmed,setKillArmed]=useState(false)
  // The pane is rendered unkeyed as the stack's single active child, so Preact
  // reuses this component instance across tab switches rather than remounting it.
  // Every piece of per-session UI state must therefore be cleared explicitly:
  // otherwise "Copy reply" copies (and records into clipboard history) the
  // previous session's reply, and the prepared-clipboard, selection and find
  // state bleed across the switch too.
  useEffect(()=>{
    setLastReply('')
    setPreparedClipboard('')
    setManualClipboard(false)
    setSelectionText('')
    setClipboardStatus('')
    setManualPaste(false)
    setFindOpen(false)
    setFindQuery('')
    setFindResult('')
    setKillArmed(false)
    setInputOwnership(UNOWNED)
    setLetterboxSize('')
    appOffTailRef.current=false
    setAppOffTail(false)
  },[session.id])
  useEffect(()=>{
    const on=(event:Event)=>setKillArmed((event as CustomEvent<string|null>).detail===session.id)
    window.addEventListener('mux:kill-armed',on)
    return()=>window.removeEventListener('mux:kill-armed',on)
  },[session.id])
  const manualClipboardRef=useRef<HTMLTextAreaElement>(null)
  const manualPasteRef=useRef<HTMLTextAreaElement>(null)
  const mobileLiveInputRef=useRef<HTMLTextAreaElement>(null)
  const focusTerminalInputRef=useRef<()=>void>(()=>{})
  // Read/select mode: when on (touch only), tapping the terminal no longer raises the soft
  // keyboard, so you can select, scroll, and paste without it. The ref is what the pointer
  // and focus closures created in the mount effect read at call time.
  const keyboardOffRef=useRef(false)
  const [keyboardOff,setKeyboardOff]=useState(false)
  // Set inside the mount effect; lets a layout change outside the ResizeObserver's reach
  // (the voice strip appearing/vanishing under the terminal) force an xterm re-fit.
  const scheduleFitRef=useRef<()=>void>(()=>{})
  // Set inside the mount effect; lets the connection overlay force an immediate attempt
  // instead of leaving the user waiting on a backoff they cannot see.
  const reconnectNowRef=useRef<()=>void>(()=>{})
  // Set inside the mount effect; sends a key that steers this session's view rather than
  // typing into it (jump-to-latest). Deliberately not the `sendKey` path: that one is typing,
  // so it is broadcast to every member, and one pane's scroll position is nobody else's.
  const sendViewKeyRef=useRef<(sequence:string)=>void>(()=>{})
  const lastAutoCopiedSelectionRef=useRef('')
  const clipboardStatusTimerRef=useRef<number|null>(null)
  const stateRef=useRef(session.state)
  stateRef.current=session.state
  const backendRef=useRef(session.backend)
  backendRef.current=session.backend
  const broadcastRef = useRef(broadcast)
  broadcastRef.current = broadcast
  // A warm pane is mounted and live while hidden, so "is this pane being looked at"
  // is a second, independent axis from `document.hidden`. Everything that used to
  // ask the document alone now asks `paneIsHidden`, which is either.
  const visibleRef = useRef(visible)
  // Set inside the mount effect; re-registers or deregisters this pane's viewport
  // when it is shown or hidden, and repaints on the way back in.
  const paneVisibilityRef = useRef<(next:boolean)=>void>(()=>{})
  useEffect(()=>{
    if(visibleRef.current===visible)return
    visibleRef.current=visible
    paneVisibilityRef.current(visible)
  },[visible])

  const prepareClipboardFallback = (text:string) => {
    setPreparedClipboard(text)
    setManualClipboard(mobileClipboardFallback())
    requestAnimationFrame(()=>{manualClipboardRef.current?.focus();manualClipboardRef.current?.select()})
  }
  const showClipboardStatus = (message:string) => {
    setClipboardStatus(message)
    if(clipboardStatusTimerRef.current!==null)window.clearTimeout(clipboardStatusTimerRef.current)
    clipboardStatusTimerRef.current=window.setTimeout(()=>setClipboardStatus(''),1800)
  }

  useEffect(() => () => {
    if(clipboardStatusTimerRef.current!==null)window.clearTimeout(clipboardStatusTimerRef.current)
  }, [])

  useEffect(() => {
    const openFind = (event: Event) => {
      if ((event as CustomEvent<string | null>).detail !== session.id) return
      setFindOpen(true)
      setMenu(null)
    }
    window.addEventListener('mux:terminal-find', openFind)
    return () => window.removeEventListener('mux:terminal-find', openFind)
  }, [session.id])

  useEffect(() => {
    if (!menu) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const frame = requestAnimationFrame(() => document.querySelector<HTMLButtonElement>('.terminal-menu button:not(:disabled)')?.focus())
    const dismissOutside = (event: PointerEvent) => {
      const target = event.target
      if (!(target instanceof Element) || !target.closest('.terminal-menu')) setMenu(null)
    }
    const dismissEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      setMenu(null)
    }
    const navigate = (event: KeyboardEvent) => {
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
      const buttons = [...document.querySelectorAll<HTMLButtonElement>('.terminal-menu button:not(:disabled)')]
      if (!buttons.length) return
      event.preventDefault()
      const current = buttons.indexOf(document.activeElement as HTMLButtonElement)
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
        : (Math.max(current, 0) + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
      buttons[next].focus()
    }
    document.addEventListener('pointerdown', dismissOutside)
    window.addEventListener('keydown', dismissEscape, true)
    window.addEventListener('keydown', navigate, true)
    return () => {
      cancelAnimationFrame(frame)
      document.removeEventListener('pointerdown', dismissOutside)
      window.removeEventListener('keydown', dismissEscape, true)
      window.removeEventListener('keydown', navigate, true)
      previous?.focus()
    }
  }, [menu])

  useEffect(() => {
    if (!host.current) return
    // Either axis hides this pane: the browser tab is in the background, or this pane
    // is a warm one behind another tab in its own stack. Nothing downstream cares
    // which — a hidden pane must not size the PTY, claim the keyboard, or write the
    // system clipboard, and both cases are hidden for exactly those purposes.
    const paneIsHidden = () => document.hidden || !visibleRef.current
    const reportStartup = (milestone: StartupMilestone) => {
      if (startupOrigin !== undefined) onStartupTiming?.(milestone, performance.now() - startupOrigin)
    }
    reportStartup('pane_mounted')
    const term = new Terminal({
      cursorBlink: true, cursorStyle: 'bar', fontFamily: '"Cascadia Mono", Consolas, monospace',
      fontSize: BASE_FONT_SIZE, fontWeight: '600', fontWeightBold: '600', lineHeight: 1.2, scrollback, allowProposedApi: true,
      screenReaderMode: false,
      // Passed at construction, not assigned later: below ConPTY build 21376 this
      // both disables reflow and installs the wrapped-line heuristic in the parser,
      // so bytes written before it is set would keep wrong wrap flags for the rest
      // of the buffer's life. `undefined` leaves xterm's cross-platform defaults.
      ...(windowsPty ? { windowsPty } : {}),
      theme: terminalThemes[resolvedTheme((document.documentElement.dataset.themeSelection || 'dark') as ThemeName)],
    })
    const fit = new FitAddon()
    const search = new SearchAddon()
    term.loadAddon(fit)
    term.loadAddon(search)
    term.loadAddon(new WebLinksAddon((event,uri)=>{
      const previewUrl=localPreviewUrl(uri)
      if(previewUrl){
        event.preventDefault()
        window.dispatchEvent(new CustomEvent('mux:open-terminal-preview',{detail:{sessionId:session.id,url:previewUrl}}))
        return
      }
      window.open(uri,'_blank','noopener,noreferrer')
    }))
    term.loadAddon(new ClipboardAddon(undefined,new ResilientClipboardProvider(
      prepareClipboardFallback,
      reportError,
      undefined,
      // Drop OSC 52 clipboard writes that arrive from replayed scrollback (a cold
      // attach replays the buffer through term.write) or while this pane is not the
      // one being looked at. Only a live, visible copy should reach the system
      // clipboard — and a warm pane is exactly a live session writing into a tab the
      // user is not on. `replaying` is declared below but is only read when a
      // sequence actually arrives, long after connect() runs.
      () => replaying || paneIsHidden(),
    )))
    term.loadAddon(new Unicode11Addon())
    term.unicode.activeVersion = '11'
    termRef.current = term
    searchRef.current = search
    term.open(host.current)
    const mobileLiveInput=isMobileTerminalInput()?mobileLiveInputRef.current:null
    // The live-input textarea is uncontrolled, and switching stack tabs re-runs this
    // effect against the *same* DOM node (the pane is rendered unkeyed as the stack's
    // only active child, so Preact reuses the instance). The IME delta baseline below
    // starts empty on every run, so any text left in the element would be re-sent in
    // full on the next keystroke — duplicating the composer contents, or leaking the
    // previous tab's text into this session. Element and baseline must start in sync.
    if(mobileLiveInput)mobileLiveInput.value=''
    const focusTerminalInput=()=>{
      if(keyboardOffRef.current)return
      if(mobileLiveInput){
        mobileLiveInput.focus({preventScroll:true})
        const end=mobileLiveInput.value.length
        mobileLiveInput.setSelectionRange(end,end)
      }else term.focus()
    }
    focusTerminalInputRef.current=focusTerminalInput
    const onTheme = (event: Event) => {
      const name = (event as CustomEvent<Exclude<ThemeName,'system'>>).detail
      term.options.theme = terminalThemes[name]
    }
    window.addEventListener('mux:theme', onTheme)
    term.attachCustomKeyEventHandler(event => {
      const decision = terminalKeyDecision(event, keybindings[keyChord(event)], term.hasSelection(), session.backend)
      if (decision.kind === 'sendInput') {
        event.preventDefault()
        // term.input keeps the write on the normal onData path, so broadcast membership
        // and the replay guard treat it exactly like a typed key.
        term.input(decision.data, true)
        return false
      }
      if (decision.kind === 'command') {
        event.preventDefault()
        event.stopPropagation()
        runCommand(decision.command)
        return false
      }
      if (decision.kind === 'browserPaste') {
        // Suppress xterm's Ctrl+V control byte but leave the browser default untouched.
        // The resulting native paste event is the single owner of clipboard payloads.
        return false
      }
      if (decision.kind === 'copySelection') {
        const text = term.getSelection()
        captureCopy(text, 'terminal')
        void copyPreparedText(text).then(copied => {
          if (copied) { term.clearSelection(); showClipboardStatus('Copied') }
          else prepareClipboardFallback(text)
        })
        return false
      }
      return true
    })
    let socket:WebSocket|null=null
    let reconnectTimer:number|undefined
    let reconnectAttempt=0
    let reconnectReplay=false
    let disposed=false
    let replyRefreshTimer:number|undefined
    // Connection-attempt bookkeeping for the liveness watcher: when the current attempt
    // started, when the backoff retry is due, and the watchdog that fails a handshake
    // which never completes (see liveness.ts for why that is the important case).
    let attemptStartedAt:number|null=null
    let nextAttemptAt:number|null=null
    let handshakeTimer:number|undefined
    let replaying = false
    let replayEndReceived = false
    let replayAllowsTerminalResponses = false
    let pendingReplayWrites = 0
    // Keystrokes typed while the buffer replays. Returning to a tab always re-attaches
    // (and a long absence reconnects), so the tap-and-type right after coming back lands
    // mid-replay; sending it then would race the replayed bytes, and dropping it loses
    // characters the mobile IME baseline has already advanced past. Hold and flush in order.
    const pendingUserInput: { data: string; broadcast: boolean }[] = []
    let pendingUserInputLength = 0
    let currentRevision = -1
    let lastReplyTriggerState=session.state
    let exitWritten = false
    let fitFrame = 0
    let redrawFrame = 0
    let surfaceFrame = 0
    let surfaceConfirmTimer: number | undefined
    let invalidateAtlasOnRedraw = false
    let webgl: WebglAddon | null = null
    let activeRenderer: ActiveTerminalRenderer = 'dom'
    let awaitingFullRedraw = false
    const diagnoseRender = terminalRenderDiagnosticsEnabled
      ? (phase: string, detail?: Record<string, unknown>) => recordTerminalRenderDiagnostic(session.id, phase, detail)
      : undefined
    const renderDiagnostic = terminalRenderDiagnosticsEnabled ? term.onRender(event => {
      if (!awaitingFullRedraw) return
      awaitingFullRedraw = false
      diagnoseRender?.('full_redraw_rendered', {
        start: event.start,
        end: event.end,
        cols: term.cols,
        rows: term.rows,
        renderer: activeRenderer,
      })
    }) : null
    const onRenderError = terminalRenderDiagnosticsEnabled ? (event: ErrorEvent) => {
      if (!isWebglRenderError(event)) return
      diagnoseRender?.('webgl_render_error', { message: event.message, renderer: activeRenderer })
    } : null
    if (onRenderError) window.addEventListener('error', onRenderError)
    const loadLatestReply=()=>{
      if(session.backend==='shell')return
      void api<{text:string}>('GET',`/api/sessions/${session.id}/last-reply`).then(result=>{
        // Clear on an empty result too: keeping the previous value here is what
        // let a session with no reply yet still answer "Copy reply".
        if(!disposed)setLastReply(result.text||'')
      }).catch(()=>{if(!disposed)setLastReply('')})
    }
    const scheduleLatestReply=(delay=700)=>{
      if(session.backend==='shell')return
      if(replyRefreshTimer!==undefined)window.clearTimeout(replyRefreshTimer)
      replyRefreshTimer=window.setTimeout(()=>{replyRefreshTimer=undefined;loadLatestReply()},delay)
    }
    const reportTerminalState = () => {
      if (socket?.readyState !== WebSocket.OPEN) return
      socket.send(JSON.stringify({ type: 'terminal_state', mode: term.buffer.active.type }))
    }
    const bufferChange = term.buffer.onBufferChange(reportTerminalState)
    // Jump-to-latest state. Scrolling up on a phone leaves no cheap way back to
    // the tail, and output arriving while scrolled up moves `baseY` without
    // moving the viewport, so this is checked on render (not only on scroll) —
    // O(1) with an early return, and it only re-renders on the actual flip.
    const syncTail = () => {
      const buffer = term.buffer.active
      const off = buffer.type === 'normal' && buffer.viewportY < buffer.baseY
      if (off === offTailRef.current) return
      offTailRef.current = off
      setOffTail(off)
    }
    const tailScroll = term.onScroll(syncTail)
    const tailRender = term.onRender(syncTail)
    const terminalStateTimer = window.setInterval(reportTerminalState, 5000)
    // Shared-PTY geometry. `localFit` is what this pane would show at its own font size;
    // `serverGeometry` is what the daemon arbitrated across every attached device. They
    // differ exactly when another device owns input, and then this pane letterboxes
    // rather than pushing its own dimensions back at the PTY.
    let localFit: { cols: number; rows: number } | null = null
    let localFitBox: { width: number; height: number } | null = null
    let serverGeometry: { cols: number; rows: number } | null = null
    let sentViewport: { cols: number; rows: number; hidden: boolean } | null = null
    let letterboxed = false
    const sendViewport = (cols: number, rows: number, force = false) => {
      if (socket?.readyState !== WebSocket.OPEN) return
      const hidden = paneIsHidden()
      if (!force && sentViewport?.cols === cols && sentViewport.rows === rows && sentViewport.hidden === hidden) return
      sentViewport = { cols, rows, hidden }
      // `hidden` deregisters this viewport instead of registering it: a minimized
      // window still reports layout, and it must not reshape the PTY for the device
      // the user is actually holding.
      socket.send(JSON.stringify({ type: 'resize', cols, rows, hidden }))
    }
    const measureFit = () => {
      const box = host.current
      if (!box) return
      const size = { width: box.clientWidth, height: box.clientHeight }
      // While letterboxed, measuring means briefly restoring the pane's own font, so it
      // is done only when the box it has to fit actually changed.
      if (letterboxed && localFit && localFitBox?.width === size.width && localFitBox.height === size.height) {
        sendViewport(localFit.cols, localFit.rows)
        return
      }
      // A letterboxed pane renders at a smaller font, and a proposal measured there
      // would tell the daemon this pane wants columns nobody on this device could read
      // — and that is the number the shared PTY would then be sized to.
      const fontSize = term.options.fontSize ?? BASE_FONT_SIZE
      if (fontSize !== BASE_FONT_SIZE) term.options.fontSize = BASE_FONT_SIZE
      const proposed = fit.proposeDimensions()
      if (fontSize !== BASE_FONT_SIZE) term.options.fontSize = fontSize
      if (!proposed || !Number.isFinite(proposed.cols) || !Number.isFinite(proposed.rows)) return
      localFit = { cols: proposed.cols, rows: proposed.rows }
      localFitBox = size
      sendViewport(localFit.cols, localFit.rows)
    }
    const applyLetterbox = (target: { cols: number; rows: number }) => {
      const box = host.current
      const screen = term.element?.querySelector<HTMLElement>('.xterm-screen')
      if (!box || !screen) return
      if (term.cols !== target.cols || term.rows !== target.rows) term.resize(target.cols, target.rows)
      const fitFont = (fontSize: number) => letterboxFontSize({
        fontSize,
        cellWidth: screen.getBoundingClientRect().width / Math.max(term.cols, 1),
        cellHeight: screen.getBoundingClientRect().height / Math.max(term.rows, 1),
        cols: target.cols,
        rows: target.rows,
        hostWidth: box.clientWidth,
        hostHeight: box.clientHeight,
        baseFontSize: BASE_FONT_SIZE,
      })
      const fontSize = term.options.fontSize ?? BASE_FONT_SIZE
      const next = fitFont(fontSize)
      if (next === fontSize) return
      term.options.fontSize = next
      // Cell metrics are not exactly proportional to font size, so the first estimate
      // can still overflow by a pixel or two. One correction pass, and only ever
      // downwards, so this cannot oscillate.
      const corrected = fitFont(next)
      if (corrected < next) term.options.fontSize = corrected
    }
    // A new socket knows nothing about the shared geometry, so the pane goes back to
    // its own font before it measures: fitting while still shrunk to another device's
    // grid would report columns this pane cannot actually show.
    const resetLetterbox = () => {
      localFit = null
      localFitBox = null
      if (!letterboxed) return
      letterboxed = false
      setLetterboxSize('')
      term.options.fontSize = BASE_FONT_SIZE
    }
    const applyGeometry = () => {
      if (serverGeometry && !geometryMatchesFit(serverGeometry, localFit)) {
        letterboxed = true
        // Set every pass, not only on the transition: the arbitrated size can change
        // while this pane stays letterboxed, and a notice naming the wrong grid is
        // worse than none.
        setLetterboxSize(`${serverGeometry.cols}×${serverGeometry.rows}`)
        applyLetterbox(serverGeometry)
        return
      }
      if (letterboxed) {
        letterboxed = false
        setLetterboxSize('')
        term.options.fontSize = BASE_FONT_SIZE
      }
      refitVisibleTerminal(fit, host.current)
    }
    const runViewportPass = () => {
      window.cancelAnimationFrame(fitFrame)
      window.cancelAnimationFrame(redrawFrame)
      fitFrame = window.requestAnimationFrame(() => {
        // Deregistering has to happen even when the pane has no layout to measure,
        // otherwise a hidden client's last size would hold the PTY hostage. A warm
        // pane has layout of its own (it is only `display:none`), so without this it
        // would keep the PTY sized for a tab nobody is looking at.
        if (paneIsHidden()) sendViewport(localFit?.cols ?? term.cols, localFit?.rows ?? term.rows)
        if (!terminalHostIsVisible(host.current)) return
        // A resize moves `baseY` (a ConPTY-backed buffer gains blank rows rather than
        // pulling scrollback back down), so a viewport that was on the newest line no
        // longer is. Remembered before the fit and restored after, because "was the
        // user reading the tail" is not answerable once the grid has changed — and
        // yanking someone who had deliberately scrolled up is worse than not.
        const wasAtTail = !offTailRef.current
        const startedAt = performance.now()
        measureFit()
        applyGeometry()
        // Timed around the two calls that do the work, so the scheduler's decision to
        // coalesce is based on this pane's real cost rather than on its backend.
        viewportScheduler.observeCost(performance.now() - startedAt)
        if (wasAtTail) scrollTerminalToTail(term)
        // DOM/WebGL pixels can be discarded while a pane or browser tab is hidden.
        // Repaint one frame after layout settles so every terminal row is invalidated.
        redrawFrame = window.requestAnimationFrame(() => {
          const fullRedraw = invalidateAtlasOnRedraw
          if (fullRedraw) webgl?.clearTextureAtlas()
          invalidateAtlasOnRedraw = false
          if (fullRedraw && terminalRenderDiagnosticsEnabled) {
            awaitingFullRedraw = true
            diagnoseRender?.('full_redraw_issued', {
              cols: term.cols,
              rows: term.rows,
              renderer: activeRenderer,
            })
          }
          redrawVisibleTerminal(term, host.current)
          armSurfaceConfirmation(fullRedraw)
        })
      })
    }
    // Repaint the surface again, without touching geometry.
    //
    // Deliberately not another viewport pass: a fit is `term.resize` plus a pseudoconsole
    // resize plus the CLI repainting everything it is showing (see EXPENSIVE_VIEWPORT_PASS_MS),
    // and none of that is what needs repeating. Only the two calls that put pixels back do.
    const runSurfaceRedraw = () => {
      surfaceFrame = window.requestAnimationFrame(() => {
        if (!terminalHostIsVisible(host.current)) return
        webgl?.clearTextureAtlas()
        redrawVisibleTerminal(term, host.current)
        diagnoseRender?.('surface_redraw_confirmed', {
          cols: term.cols,
          rows: term.rows,
          renderer: activeRenderer,
        })
      })
    }
    // A full redraw races the compositor: the frame it lands on may be one where this pane's
    // canvas is not being presented at its final size yet, and the render is then simply lost
    // — xterm's RenderService fires `onRender` whether or not the renderer drew anything, so
    // nothing anywhere retries it. One confirmation after the burst has settled costs an atlas
    // clear and covers that, and unlike the redraw itself it cannot be raced by layout.
    const armSurfaceConfirmation = (issued: boolean) => {
      if (!issued) return
      window.clearTimeout(surfaceConfirmTimer)
      surfaceConfirmTimer = window.setTimeout(runSurfaceRedraw, VIEWPORT_SETTLE_MS)
    }
    const viewportScheduler = createViewportScheduler(runViewportPass, {
      now: () => performance.now(),
      setTimer: (fn, ms) => window.setTimeout(fn, ms),
      clearTimer: id => window.clearTimeout(id),
    })
    const scheduleViewport = (invalidateAtlas: boolean, burst = false) => {
      invalidateAtlasOnRedraw ||= invalidateAtlas
      if (invalidateAtlas) diagnoseRender?.('full_redraw_requested', { pendingReplayWrites })
      viewportScheduler.request(burst)
    }
    const scheduleFit = () => scheduleViewport(false)
    // Resize floods: a soft keyboard fires `visualViewport` resizes through its whole
    // open/close animation, and every one of them changes `--app-height`, which the
    // host's ResizeObserver sees too. Fitting per frame is what made opening the
    // keyboard on a long Codex session lag and then visibly scroll: each pass resized
    // the pseudoconsole, so the CLI repainted its entire scrollback-mode transcript,
    // ~20 times over. The scheduler runs the first one and coalesces the rest.
    const scheduleBurstFit = () => scheduleViewport(false, true)
    const scheduleFullRedraw = () => scheduleViewport(true)
    scheduleFitRef.current = scheduleFullRedraw
    paneVisibilityRef.current = (nowVisible: boolean) => {
      if (!nowVisible) {
        // Hidden by `display:none`, so the host measures zero and the scheduled fit
        // would return before deregistering. Send the deregistration directly.
        // Unconditional in the dimensions: `sendViewport` reads `hidden` itself, so what
        // matters is that the frame goes at all. Gating it on a `localFit` this pane may
        // never have taken (a reconnect nulls it) is what let a registration outlive the
        // pane's own visibility and hold the PTY at a size nobody was looking at.
        sendViewport(localFit?.cols ?? term.cols, localFit?.rows ?? term.rows)
        return
      }
      // Back on screen. The pane may have missed geometry changes while it had no
      // layout to measure, and its renderer may hold pixels from before the tab was
      // resized, so this is a full redraw rather than a plain fit. The tail scroll is
      // the same repair `finishReplay` does: output that arrived while the pane was
      // hidden moved `baseY` without moving a viewport nobody was watching.
      scheduleFullRedraw()
      window.requestAnimationFrame(() => scrollTerminalToTail(term))
    }
    // Chromium device emulation can preserve a live WebGL context while changing
    // its emulated pixel ratio, leaving xterm interactive but visually blank.
    // The built-in renderer is reliable for the single full-screen mobile pane.
    const mobileRenderer = window.matchMedia('(max-width:760px)').matches
    // Codex's full-screen redraws can corrupt WebGL scrollback while the
    // viewport is off-tail. Its DOM renderer remains stable for old sessions;
    // new sessions also start Codex in raw scrollback mode on the backend.
    if (shouldLoadWebgl(rendererPreference, mobileRenderer, session.backend)) {
      try {
        // `preserveDrawingBuffer: true`, and it is not optional here.
        //
        // WebglRenderer._updateModel skips any cell whose code, fg, bg and ext all match
        // its model ("Nothing has changed, no updates needed"), so a frame re-uploads only
        // what changed and every other pixel is expected to still be in the drawing buffer.
        // With the default `false` the spec lets the browser discard that buffer once the
        // canvas stops being composited — which is exactly what a warm pane behind another
        // tab is, since `.pane-warm` is `display:none`. Coming back, the model still claims
        // everything is drawn, so only genuinely-changed cells repaint and the rest stay
        // blank. Dragging a selection over them changes their fg/bg, which fails that
        // equality check and makes them reappear: the "it draws once I highlight it" report.
        //
        // The repair below still exists, because a lost context and a dimension change need
        // it regardless. But no event fires when a compositor drops a drawing buffer, so a
        // repair keyed on events cannot be complete and the assumption has to go instead.
        const addon = new WebglAddon(true)
        webgl = addon
        addon.onContextLoss(() => {
          if (webgl !== addon) return
          webgl = null
          activeRenderer = 'dom'
          addon.dispose()
          diagnoseRender?.('webgl_context_lost')
          scheduleFullRedraw()
        })
        term.loadAddon(addon)
        activeRenderer = 'webgl'
      } catch (error) {
        webgl = null
        activeRenderer = 'dom'
        diagnoseRender?.('webgl_load_failed', { message: error instanceof Error ? error.message : String(error) })
        // The built-in DOM renderer remains active on machines without WebGL support.
      }
    }
    // Establish final geometry after the selected renderer is active and before
    // the socket can start replaying the terminal buffer.
    const preconnectFit = refitVisibleTerminal(fit, host.current)
    diagnoseRender?.('preconnect_fit', {
      fitted: preconnectFit,
      cols: term.cols,
      rows: term.rows,
      renderer: activeRenderer,
    })
    let ownsInput = false
    let ownership: OwnershipView = UNOWNED
    // The same device class the presence heartbeat reports, deliberately: the daemon
    // compares these two strings, and `isMobileTerminalInput()` is a *different*
    // question (does this pane need the IME bridge — true for any coarse pointer,
    // including one wider than the mobile breakpoint). A phone that answered "mobile"
    // here and "desktop" there would report itself in use and then refuse its own
    // claims for being the other device.
    const device = currentProfile()
    // When the user last did something in this pane. A focus event within the gesture
    // window is the user asking for the keyboard; anything later is the pane restoring
    // its own focus, which must not take input from another device.
    let lastInteractionAt: number | null = null
    // When this pane last asked for input back on its own. Bounds the re-claim so no
    // frame the daemon sends can put a pane into a claim loop.
    let lastReclaimAt: number | null = null
    // One retry per socket, once the daemon has been told which device this is. See
    // onPresenceReported: the pane's claim usually beats the presence heartbeat on a
    // cold load, and is judged against a stale idea of where the user is.
    let presenceRetried = false
    const noteOwnership = (next: OwnershipView) => {
      ownership = next
      ownsInput = next.owns
      setInputOwnership(next)
    }
    // Per device class, because `hasFocus()` is a desktop concept a phone answers
    // inconsistently — see deviceIsFocused. Reporting it raw made the phone's every
    // claim look like it came from a background window.
    const paneIsFocused = () => deviceIsFocused({
      profile: device,
      visible: !paneIsHidden(),
      // A hidden pane must never look focused: input arbitration hands the keyboard
      // to whoever answers true, and a warm pane in a background tab has as much
      // claim to it as a minimized window does.
      hasFocus: !paneIsHidden() && document.hasFocus(),
    })
    const claimInput = (reason: ClaimReason) => {
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({
          type: 'claim_input',
          reason,
          device,
          // A minimized or backgrounded window answers false and so cannot passively
          // take the keyboard from the device in front of the user.
          focused: paneIsFocused(),
        }))
      }
      // Focus here also makes this terminal the target for inserted text (clipboard
      // history, prompt templates) once an overlay has taken DOM focus away.
      noteTerminalFocus(session.id)
    }
    claimInputRef.current = (reason: ClaimReason) => { lastInteractionAt = Date.now(); claimInput(reason) }
    resizeToPaneRef.current = () => {
      const box = host.current
      if (paneIsHidden() || !terminalHostIsVisible(box)) return
      // A letterboxed pane is using another device's font/grid. Restore the base
      // font, synchronously fit the box the user can see, and force a viewport frame
      // before the gesture claim. WebSocket ordering makes that registration the
      // geometry the daemon applies whether this client already owns input or takes it
      // from another client with the following claim.
      resetLetterbox()
      if (!refitVisibleTerminal(fit, box)) return
      localFit = { cols: term.cols, rows: term.rows }
      localFitBox = { width: box.clientWidth, height: box.clientHeight }
      serverGeometry = localFit
      sendViewport(localFit.cols, localFit.rows, true)
      lastInteractionAt = Date.now()
      claimInput('gesture')
      runSurfaceRedraw()
    }
    const claimOnFocus = () => claimInput(claimReasonForFocus(lastInteractionAt, Date.now()))
    // The events socket and this one race on a cold load, and this one usually wins,
    // so the daemon judges the attach claim without yet knowing which device asked.
    // Ask once more the moment it does. Once per socket: after that the refusal is a
    // real answer and the take-over strip is the honest response to it.
    const onPresenceReported = () => {
      if (presenceRetried || ownsInput || ownership.denied === null) return
      presenceRetried = true
      claimInput('passive')
    }
    const clearHandshakeWatchdog=()=>{
      if(handshakeTimer===undefined)return
      window.clearTimeout(handshakeTimer)
      handshakeTimer=undefined
    }
    const scheduleReconnect=()=>{
      if(disposed||['exited','crashed'].includes(stateRef.current)||reconnectTimer!==undefined)return
      setConnectionState('reconnecting')
      const delay=retryDelay(reconnectAttempt)
      reconnectAttempt+=1
      nextAttemptAt=Date.now()+delay
      reconnectTimer=window.setTimeout(()=>{reconnectTimer=undefined;nextAttemptAt=null;connect(true)},delay)
    }
    const sendInput = (data: string, protocolResponse: boolean, broadcast: boolean, retry = false) => {
      if (socket?.readyState !== WebSocket.OPEN) return
      // Typing is itself evidence this pane should own input. Without it a pane
      // displaced by another device stays silently muted until the user happens
      // to click inside it. A retry has already claimed, so it must not claim twice.
      if (!protocolResponse) lastInteractionAt = Date.now()
      if (!ownsInput && !protocolResponse && !retry) claimInput('gesture')
      socket.send(JSON.stringify({
        type: 'input',
        data,
        kind: protocolResponse ? 'terminal_response' : 'user',
        broadcast: protocolResponse ? false : broadcast,
        retry,
      }))
    }
    // Keystrokes the daemon refused because this pane had lost input ownership without
    // knowing it yet — the race that used to eat a large fraction of everything typed
    // on a phone while a desktop pane was still attached. Re-claim and resend once;
    // if that is refused too, the other device really is in use and the pane says so.
    const replayRejectedInput = (frame: { data?: string; broadcast?: boolean; retry?: boolean }) => {
      noteOwnership(applyRejectedFrame(ownership, frame))
      if (!shouldReplayRejectedInput(frame)) return
      claimInput('gesture')
      sendInput(String(frame.data), false, frame.broadcast === true, true)
    }
    const finishReplay = () => {
      replaying = false
      replayAllowsTerminalResponses = false
      const queued = pendingUserInput.splice(0, pendingUserInput.length)
      pendingUserInputLength = 0
      for (const item of queued) sendInput(item.data, false, item.broadcast)
      scheduleFullRedraw()
    }
    const handleMessage=(event:MessageEvent)=>{
      if (event.data instanceof ArrayBuffer) {
        scheduleLatestReply()
        if (replaying) {
          pendingReplayWrites += 1
          term.write(new Uint8Array(event.data), () => {
            pendingReplayWrites -= 1
            if (replayEndReceived && pendingReplayWrites === 0) finishReplay()
          })
        } else term.write(new Uint8Array(event.data))
      }
      else {
        const frame = JSON.parse(event.data)
        if (frame.type === 'gap') replaying = true
        if ((frame.type === 'state' || frame.type === 'update') && Number(frame.revision ?? 0) > currentRevision) {
          currentRevision = Number(frame.revision ?? 0)
          const nextState=frame.snapshot?.state as Session['state']|undefined
          if(nextState&&['idle','awaiting'].includes(nextState)&&nextState!==lastReplyTriggerState)scheduleLatestReply(250)
          if(nextState)lastReplyTriggerState=nextState
          onState(frame.snapshot)
        }
        if (frame.type === 'replay_start') {
          if (frame.reason === 'resync' || reconnectReplay) term.reset()
          reconnectReplay=false
          replaying = true
          replayEndReceived = false
          replayAllowsTerminalResponses = frame.reason === 'attach' && frame.allow_terminal_responses === true
        }
        if (frame.type === 'replay_end') {
          reportStartup('replay_ready')
          replayEndReceived = true
          if (pendingReplayWrites === 0) finishReplay()
        }
        if (frame.type === 'input_owner') {
          // Ownership is claimed on pointerdown, so a device that never receives
          // another pointer event (the desktop pane still has DOM focus after the
          // phone claimed the session) would type into a void: the daemon refuses
          // every keystroke from a non-owner. Re-claim as soon as this pane is the
          // focused one again — but only if it really is. `document.activeElement`
          // survives minimizing the window, and reading that as intent is what let a
          // background desktop pane take the keyboard back from the phone forever.
          noteOwnership(applyOwnerFrame(ownership, frame))
          if (!ownsInput && shouldReclaimAfterDisplacement({
            reason: typeof frame.reason === 'string' ? frame.reason : null,
            focusInHost: !!document.activeElement && host.current?.contains(document.activeElement) === true,
            paneHidden: paneIsHidden(),
            windowFocused: paneIsFocused(),
            lastReclaimAt,
            now: Date.now(),
          })) {
            lastReclaimAt = Date.now()
            claimInput('passive')
          }
        }
        if (frame.type === 'input_owner_released') noteOwnership(applyOwnerReleased(ownership, frame.epoch))
        if (frame.type === 'input_rejected') replayRejectedInput(frame)
        if (frame.type === 'geometry') {
          const cols = Number(frame.cols)
          const rows = Number(frame.rows)
          if (Number.isFinite(cols) && Number.isFinite(rows)) {
            serverGeometry = { cols, rows }
            scheduleFit()
          }
        }
        if (frame.type === 'exit') {
          setConnectionState('ended')
          if (frame.snapshot && Number(frame.revision ?? 0) >= currentRevision) {
            currentRevision = Number(frame.revision ?? 0)
            onState(frame.snapshot)
          }
          if (!exitWritten) {
            exitWritten = true
            term.writeln('\r\n\x1b[38;5;243m[process exited]\x1b[0m')
          }
        }
      }
    }
    const connect=(reconnecting:boolean)=>{
      if(disposed||['exited','crashed'].includes(stateRef.current))return
      if(reconnectTimer!==undefined){clearTimeout(reconnectTimer);reconnectTimer=undefined}
      nextAttemptAt=null
      clearHandshakeWatchdog()
      reconnectReplay=reconnecting
      presenceRetried=false
      setConnectionState(reconnecting?'reconnecting':'connecting')
      attemptStartedAt=Date.now()
      let next:WebSocket
      // Constructing a socket can throw outright (no route, blocked scheme). That must feed
      // the retry path rather than escape into the caller's timer.
      try{next=openWebSocket(`/pty/${session.id}`)}catch{socket=null;scheduleReconnect();return}
      next.binaryType='arraybuffer'
      socket=next
      // A handshake that stalls (resumed PWA, route gone) never fires close or error, so
      // without this the pane would sit on "reconnecting…" forever.
      handshakeTimer=window.setTimeout(()=>{
        handshakeTimer=undefined
        if(socket!==next||next.readyState===WebSocket.OPEN)return
        next.onclose=null;next.onerror=null;next.onmessage=null
        try{next.close()}catch{/* already tearing down */}
        socket=null
        scheduleReconnect()
      },HANDSHAKE_TIMEOUT_MS)
      next.onopen=()=>{
        if(socket!==next)return
        clearHandshakeWatchdog()
        reconnectAttempt=0
        setConnectionState('connected')
        if(!reconnecting)reportStartup('socket_open')
        // A new socket carries none of the old one's arbitration state: the daemon has
        // no viewport for this connection and has not told it the shared geometry yet.
        sentViewport=null
        serverGeometry=null
        resetLetterbox()
        const fitted = refitVisibleTerminal(fit, host.current)
        // `document.hidden` is not this pane's visibility: a warm pane is `display:none`
        // inside a *foreground* tab, so it answered "visible", registered the 80x24 it
        // had never fitted, and — being unowned — took ownership and resized the session
        // to it. `paneIsHidden` is the pane's own axis, and the fit check covers the rest.
        const registers = attachRegistersViewport(fitted, paneIsHidden())
        // Seeded here so a pane that later goes hidden can withdraw the size it actually
        // registered, rather than waiting on a fit pass it will never be visible to run.
        if (registers) localFit = { cols: term.cols, rows: term.rows }
        next.send(JSON.stringify(terminalAttachReadyFrame(term.cols, term.rows, activeRenderer, !registers)))
        // attach_ready is itself a viewport registration; recording it keeps the fit
        // pass that follows from sending the same dimensions again.
        sentViewport={cols:term.cols,rows:term.rows,hidden:!registers}
        diagnoseRender?.('attach_ready_sent', {
          fitted,
          registers,
          cols: term.cols,
          rows: term.rows,
          renderer: activeRenderer,
        })
        scheduleFit()
        // Attaching is not the user asking for the keyboard: a passive claim takes
        // input only when nobody else is actively using this session.
        claimInput('passive')
        reportTerminalState()
        // Mobile browsers should receive focus from the user's terminal tap.
        // Pre-focusing an invisible textarea outside a gesture can leave Gboard
        // believing the field is active without actually opening the keyboard.
        if(!reconnecting&&!mobileLiveInput)term.focus()
      }
      next.onmessage=event=>{if(socket===next)handleMessage(event)}
      next.onclose=()=>{if(socket!==next)return;socket=null;scheduleReconnect()}
      next.onerror=()=>{if(socket===next)next.close()}
    }
    const reconnect=()=>{
      if(disposed||['exited','crashed'].includes(stateRef.current))return
      clearHandshakeWatchdog()
      if(socket){socket.onclose=null;socket.onerror=null;socket.onmessage=null;socket.close();socket=null}
      connect(true)
    }
    // Forced by the liveness watcher (and by tapping the overlay): the user is back, so
    // drop any pending backoff and try immediately.
    const reconnectNow=()=>{
      if(disposed||['exited','crashed'].includes(stateRef.current))return
      reconnectAttempt=0
      reconnect()
    }
    reconnectNowRef.current=reconnectNow
    const socketPhase=():ConnectionPhase=>{
      if(!socket)return 'closed'
      if(socket.readyState===WebSocket.OPEN)return 'open'
      return socket.readyState===WebSocket.CONNECTING?'connecting':'closed'
    }
    const input = term.onData(data => {
      if (shouldSuppressTerminalProtocolResponse(data, backendRef.current)) return
      if (isTerminalProtocolResponse(data)) {
        // Terminal query replies are only meaningful inside the probe window that asked
        // for them, so these are never queued: a late reply is worse than none.
        if (!replaying || replayAllowsTerminalResponses) sendInput(data, true, false)
        return
      }
      if (replaying) {
        if (pendingInputDecision(pendingUserInputLength, data.length) === 'overflow') {
          // Losing input without saying so is what made this look like the terminal
          // "eating" pastes; past the ceiling the pane owes the user an explanation.
          reportError('Input dropped: the terminal was still restoring its buffer.')
          return
        }
        pendingUserInputLength += data.length
        pendingUserInput.push({ data, broadcast: broadcastRef.current })
        return
      }
      sendInput(data, false, broadcastRef.current)
    })
    // View keys skip xterm's `input()` so they are never broadcast, and are dropped rather
    // than queued while replaying: the buffer is still being written, and a viewport gesture
    // that arrives seconds after the tap would move the user somewhere they no longer asked
    // for. It still claims input, since asking a session to scroll is this device using it.
    sendViewKeyRef.current = (sequence: string) => {
      if (replaying) return
      sendInput(sequence, false, false)
    }
    let mobileInputValue=''
    let lineBreakSent=false
    let lineBreakResetTimer:number|undefined
    const markLineBreakSent=()=>{
      lineBreakSent=true
      if(lineBreakResetTimer!==undefined)window.clearTimeout(lineBreakResetTimer)
      lineBreakResetTimer=window.setTimeout(()=>{lineBreakSent=false;lineBreakResetTimer=undefined},0)
    }
    const resetMobileInput=()=>{
      mobileInputValue=''
      if(mobileLiveInput)mobileLiveInput.value=''
    }
    const keepMobileCaretAtEnd=()=>{
      if(!mobileLiveInput)return
      const end=mobileLiveInput.value.length
      mobileLiveInput.setSelectionRange(end,end)
    }
    const mobileBeforeInput=(event:InputEvent)=>{
      if(event.inputType!=='insertLineBreak'&&event.inputType!=='insertParagraph')return
      event.preventDefault()
      if(!lineBreakSent)term.input('\r',true)
      markLineBreakSent()
      resetMobileInput()
    }
    const mobileTextInput=()=>{
      if(!mobileLiveInput)return
      const next=mobileLiveInput.value
      if(lineBreakSent&&/^[\r\n]*$/.test(next)){
        resetMobileInput();return
      }
      const data=mobileImeDelta(mobileInputValue,next)
      mobileInputValue=next
      if(data)term.input(data,true)
      if(/[\r\n]/.test(next))resetMobileInput()
      else requestAnimationFrame(keepMobileCaretAtEnd)
    }
    const mobileKeyDown=(event:KeyboardEvent)=>{
      const sequence:Record<string,string>={
        ArrowUp:'\x1b[A',ArrowDown:'\x1b[B',ArrowRight:'\x1b[C',ArrowLeft:'\x1b[D',
        Home:'\x1b[H',End:'\x1b[F',PageUp:'\x1b[5~',PageDown:'\x1b[6~',Delete:'\x1b[3~',
        Escape:'\x1b',Tab:'\t',
      }
      if(event.key==='Enter'&&!event.isComposing){
        const agentNewline=(event.shiftKey||event.ctrlKey)&&!event.altKey&&!event.metaKey&&session.backend!=='shell'
        event.preventDefault();term.input(agentNewline?AGENT_NEWLINE:'\r',true);markLineBreakSent();resetMobileInput();return
      }
      if(event.key==='Backspace'&&!mobileInputValue){
        event.preventDefault();term.input('\x7f',true);return
      }
      const data=sequence[event.key]
      if(!data)return
      event.preventDefault();term.input(data,true);resetMobileInput()
    }
    const mobilePaste=(event:ClipboardEvent)=>{
      if(!mobileLiveInput||!event.clipboardData)return
      const image=clipboardImage(Array.from(event.clipboardData.items))
      if(image&&acceptsClipboardImages(session)){
        event.preventDefault();resetMobileInput()
        void insertTerminalImage(term,session,image).catch(cause=>reportError(cause instanceof Error?cause.message:'Clipboard image paste failed.'))
        return
      }
      const text=event.clipboardData.getData('text/plain')
      if(!text)return
      event.preventDefault();resetMobileInput();pasteIntoTerminal(term,session,text);focusTerminalInput()
    }
    mobileLiveInput?.addEventListener('beforeinput',mobileBeforeInput)
    mobileLiveInput?.addEventListener('input',mobileTextInput)
    mobileLiveInput?.addEventListener('keydown',mobileKeyDown)
    mobileLiveInput?.addEventListener('paste',mobilePaste)
    const selectionChange = term.onSelectionChange(() => {
      const text=term.getSelection()
      setSelectionText(text)
      if(!text)lastAutoCopiedSelectionRef.current=''
    })
    const autoCopySelection=()=>{
      if(!mobileInput.autoCopySelection)return
      const text=term.getSelection()
      if(!text||text===lastAutoCopiedSelectionRef.current)return
      lastAutoCopiedSelectionRef.current=text
      captureCopy(text,'terminal')
      void copyPreparedText(text).then(copied=>{
        if(copied)showClipboardStatus('Selection copied')
        else prepareClipboardFallback(text)
      })
    }
    let longPress: number | null = null
    let lastTouchAt = 0
    let activePointerId:number|null=null
    let touch:{
      pointerId:number
      lastY:number
      startX:number
      startY:number
      px:number
      py:number
      moved:boolean
      selecting:{start:TerminalCell;length:number}|null
    }|null=null
    // Focus (and the soft keyboard) is deferred to release: only a still tap sets this,
    // so a scroll or selection drag never raises the keyboard mid-gesture.
    let focusOnMouseClaim=false
    let selectionScrollTimer:number|undefined
    let selectionScrollDir=0
    const stopSelectionScroll=()=>{if(selectionScrollTimer!==undefined){window.clearInterval(selectionScrollTimer);selectionScrollTimer=undefined}selectionScrollDir=0}
    const cancelLongPress = () => { if (longPress !== null) window.clearTimeout(longPress); longPress = null }
    const cellAt = (clientX:number,clientY:number) => {
      const screen = term.element?.querySelector<HTMLElement>('.xterm-screen')
      if (!screen) return null
      return terminalCellAtPoint(
        clientX,
        clientY,
        screen.getBoundingClientRect(),
        term.cols,
        term.rows,
        term.buffer.active.viewportY,
      )
    }
    const applyTouchSelection=(clientX:number,clientY:number)=>{
      if(!touch?.selecting)return
      const cell=cellAt(clientX,clientY)
      if(!cell)return
      const span=terminalSelectionSpan(touch.selecting.start,touch.selecting.length,cell,term.cols)
      term.select(span.column,span.row,span.length)
    }
    // Dragging a touch selection into the top/bottom edge scrolls the viewport on a
    // timer (finger held still fires no move events) and re-extends the selection over
    // the newly revealed rows, matching desktop drag-select.
    const updateSelectionAutoScroll=(clientY:number)=>{
      const rect=term.element?.getBoundingClientRect()
      if(!rect){stopSelectionScroll();return}
      const zone=Math.max(20,rect.height/Math.max(term.rows,1))
      const dir=clientY>rect.bottom-zone?1:clientY<rect.top+zone?-1:0
      if(dir===0){stopSelectionScroll();return}
      selectionScrollDir=dir
      if(selectionScrollTimer!==undefined)return
      selectionScrollTimer=window.setInterval(()=>{
        if(!touch?.selecting){stopSelectionScroll();return}
        const before=term.buffer.active.viewportY
        term.scrollLines(selectionScrollDir)
        if(term.buffer.active.viewportY===before){stopSelectionScroll();return}
        applyTouchSelection(touch.px,touch.py)
      },60)
    }
    const pointerClaim = (event: PointerEvent) => {
      // Touching the terminal is the user asking for the keyboard on this device, and
      // that always wins: it is the one signal no heuristic should be able to override.
      lastInteractionAt = Date.now()
      claimInput('gesture')
      activePointerId=event.pointerId
      if (event.pointerType === 'touch') {
        lastTouchAt = Date.now()
        touch={pointerId:event.pointerId,lastY:event.clientY,startX:event.clientX,startY:event.clientY,px:event.clientX,py:event.clientY,moved:false,selecting:null}
        cancelLongPress()
        if(mobileInput.longPress==='context_menu')longPress = window.setTimeout(() => {
          const cell = cellAt(event.clientX,event.clientY)
          const line = cell ? term.buffer.active.getLine(cell.row)?.translateToString(true) : ''
          if (cell && line !== undefined) {
            const word = terminalWordRange(line,cell.column)
            term.select(word.start,cell.row,Math.max(1,word.length))
            if (touch?.pointerId === event.pointerId) {
              touch.selecting={start:{column:word.start,row:cell.row},length:Math.max(1,word.length)}
            }
            navigator.vibrate?.(20)
          }
          longPress = null
        },450)
      }
    }
    const mobileMouseClaim=(event:MouseEvent)=>{
      if(Date.now()-lastTouchAt>=1500)return
      // The synthesized tap after a drag (scroll/selection) is swallowed without
      // focusing, so only a genuine tap raises the soft keyboard.
      event.preventDefault();event.stopPropagation()
      if(focusOnMouseClaim)focusTerminalInput()
    }
    const pointerMove=(event:PointerEvent)=>{
      if(event.pointerType!=='touch'||!touch||event.pointerId!==touch.pointerId)return
      if(!touch.moved&&Math.hypot(event.clientX-touch.startX,event.clientY-touch.startY)>10)touch.moved=true
      if(touch.selecting){
        touch.px=event.clientX;touch.py=event.clientY
        event.preventDefault()
        applyTouchSelection(event.clientX,event.clientY)
        updateSelectionAutoScroll(event.clientY)
        return
      }
      if(Math.abs(event.clientY-touch.startY)>8)cancelLongPress()
      const delta=touchWheelDelta(touch.lastY,event.clientY,mobileInput)
      touch.lastY=event.clientY
      if(Math.abs(delta)<1)return
      const mouseActive=term.modes.mouseTrackingMode!=='none'
      const dragTarget=mobileDragTarget(mobileInput.verticalDrag,mouseActive)
      if(dragTarget==='disabled')return
      event.preventDefault()
      if(dragTarget==='terminal'){
        const rowHeight=(term.element?.getBoundingClientRect().height??term.rows*13)/Math.max(term.rows,1)
        term.scrollLines(terminalScrollLines(delta,rowHeight))
        return
      }
      // This scroll belongs to the application, so nothing in xterm's buffer will ever record
      // it. A drag back through the history is the pane's only evidence that the user is no
      // longer looking at the newest output, and it is what raises the chip. Only a drag
      // back: reaching the newest line again is a thing only the application knows, so the
      // chip stays up until the jump is taken, rather than guessing and vanishing early.
      if(delta<0&&!appOffTailRef.current){appOffTailRef.current=true;setAppOffTail(true)}
      term.element?.dispatchEvent(new WheelEvent('wheel',{
        bubbles:true,cancelable:true,clientX:event.clientX,clientY:event.clientY,
        deltaY:delta,deltaMode:WheelEvent.DOM_DELTA_PIXEL,
      }))
    }
    const pointerEnd=(event:PointerEvent)=>{
      if(activePointerId!==event.pointerId)return
      activePointerId=null
      // A quick, still tap means "type here" and raises the keyboard; a drag (scroll or
      // selection) does not. keyboardOff mode ignores the focus regardless.
      focusOnMouseClaim=event.pointerType==='touch'&&!!touch&&!touch.selecting&&!touch.moved
      if(focusOnMouseClaim)focusTerminalInput()
      stopSelectionScroll();cancelLongPress();touch=null
      requestAnimationFrame(autoCopySelection)
    }
    const pointerCancel=(event:PointerEvent)=>{
      if(activePointerId!==event.pointerId)return
      activePointerId=null;focusOnMouseClaim=false;stopSelectionScroll();cancelLongPress();touch=null
    }
    const openMenu = (event: MouseEvent) => {
      // The terminal body has no context menu: right-click stays out of the
      // terminal surface entirely. Suppress the browser/desktop menu and the
      // touch long-press selection gesture, but never open our own menu here.
      event.preventDefault()
    }
    const pasteEvent = (event: ClipboardEvent) => {
      if (!acceptsClipboardImages(session) || !event.clipboardData) return
      const image = clipboardImage(Array.from(event.clipboardData.items))
      if (!image) return
      event.preventDefault()
      event.stopPropagation()
      void insertTerminalImage(term, session, image).catch(cause => {
        reportError(cause instanceof Error ? cause.message : 'Clipboard image paste failed.')
      })
    }
    const hasFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types || []).includes('Files')
    const dragEnter = (event: DragEvent) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      if (acceptsClipboardImages(session) && event.dataTransfer && hasTerminalImage(Array.from(event.dataTransfer.items))) {
        setImageDropActive(true)
      }
    }
    const dragOver = (event: DragEvent) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = acceptsClipboardImages(session) && hasTerminalImage(Array.from(event.dataTransfer.items)) ? 'copy' : 'none'
      }
    }
    const dragLeave = (event: DragEvent) => {
      const next = event.relatedTarget
      if (next instanceof Node && host.current?.contains(next)) return
      setImageDropActive(false)
    }
    const drop = (event: DragEvent) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      event.stopPropagation()
      setImageDropActive(false)
      if (!acceptsClipboardImages(session)) {
        reportError('Drop images into an open Claude or Codex session.')
        return
      }
      const transfer = event.dataTransfer
      const image = transfer && (clipboardImage(Array.from(transfer.items)) || Array.from(transfer.files).find(isTerminalImage))
      if (!image) {
        reportError('Supported image types are PNG, JPEG, WebP, and GIF.')
        return
      }
      void insertTerminalImage(term, session, image).catch(cause => {
        reportError(cause instanceof Error ? cause.message : 'Image drop failed.')
      })
    }
    host.current.addEventListener('pointerdown', pointerClaim)
    host.current.addEventListener('pointermove', pointerMove)
    host.current.addEventListener('mousedown',mobileMouseClaim,true)
    window.addEventListener('pointerup', pointerEnd)
    window.addEventListener('pointercancel', pointerCancel)
    // Focus alone is ambiguous: the pane focuses its own terminal on attach and on tab
    // switches. Only focus that follows a real interaction claims as a gesture.
    host.current.addEventListener('focusin', claimOnFocus)
    host.current.addEventListener('contextmenu', openMenu)
    host.current.addEventListener('paste', pasteEvent, true)
    host.current.addEventListener('dragenter', dragEnter)
    host.current.addEventListener('dragover', dragOver)
    host.current.addEventListener('dragleave', dragLeave)
    host.current.addEventListener('drop', drop)
    const observer = new ResizeObserver(scheduleBurstFit)
    observer.observe(host.current)
    const intersection = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) scheduleFullRedraw()
    })
    intersection?.observe(host.current)
    window.addEventListener('resize', scheduleBurstFit)
    // Redraw only: reconnect decisions all belong to the liveness watcher below, which
    // owns the attempt bookkeeping and can also recover from a stalled handshake.
    // Scheduled in both directions: becoming hidden is what deregisters this pane's
    // viewport, so the PTY stops being sized for a window nobody is looking at.
    const onVisibility=()=>{paneIsHidden()?scheduleFit():scheduleFullRedraw()}
    const onPageShow=()=>scheduleFullRedraw()
    const onWindowFocus=()=>scheduleFullRedraw()
    document.addEventListener('visibilitychange',onVisibility)
    window.addEventListener('pageshow',onPageShow)
    window.addEventListener('focus',onWindowFocus)
    const stopLivenessWatch=watchLiveness({
      phase:socketPhase,
      attemptStartedAt:()=>attemptStartedAt,
      nextAttemptAt:()=>nextAttemptAt,
      // An exited or crashed session has nothing left to attach to.
      enabled:()=>!disposed&&!['exited','crashed'].includes(stateRef.current),
      reconnect:reconnectNow,
    })
    window.visualViewport?.addEventListener('resize',scheduleBurstFit)
    loadLatestReply()
    window.addEventListener(PRESENCE_REPORTED_EVENT, onPresenceReported)
    connect(false)
    return () => { disposed=true;stopSelectionScroll();stopLivenessWatch();clearHandshakeWatchdog();reconnectNowRef.current=()=>{};if(reconnectTimer!==undefined)clearTimeout(reconnectTimer);if(replyRefreshTimer!==undefined)clearTimeout(replyRefreshTimer);if(lineBreakResetTimer!==undefined)clearTimeout(lineBreakResetTimer);window.clearInterval(terminalStateTimer);bufferChange.dispose();tailScroll.dispose();tailRender.dispose();renderDiagnostic?.dispose();input.dispose();selectionChange.dispose();cancelLongPress();observer.disconnect();intersection?.disconnect();window.cancelAnimationFrame(fitFrame);window.cancelAnimationFrame(redrawFrame);window.cancelAnimationFrame(surfaceFrame);window.clearTimeout(surfaceConfirmTimer);window.removeEventListener('resize',scheduleBurstFit);window.visualViewport?.removeEventListener('resize',scheduleBurstFit);viewportScheduler.cancel();document.removeEventListener('visibilitychange',onVisibility);window.removeEventListener('pageshow',onPageShow);window.removeEventListener('focus',onWindowFocus);if(onRenderError)window.removeEventListener('error',onRenderError);window.removeEventListener('pointerup',pointerEnd);window.removeEventListener('pointercancel',pointerCancel);window.removeEventListener('mux:theme',onTheme);window.removeEventListener(PRESENCE_REPORTED_EVENT,onPresenceReported);mobileLiveInput?.removeEventListener('beforeinput',mobileBeforeInput);mobileLiveInput?.removeEventListener('input',mobileTextInput);mobileLiveInput?.removeEventListener('keydown',mobileKeyDown);mobileLiveInput?.removeEventListener('paste',mobilePaste);if(mobileLiveInput)mobileLiveInput.value='';host.current?.removeEventListener('pointerdown',pointerClaim);host.current?.removeEventListener('pointermove',pointerMove);host.current?.removeEventListener('mousedown',mobileMouseClaim,true);host.current?.removeEventListener('focusin',claimOnFocus);host.current?.removeEventListener('contextmenu',openMenu);host.current?.removeEventListener('paste',pasteEvent,true);host.current?.removeEventListener('dragenter',dragEnter);host.current?.removeEventListener('dragover',dragOver);host.current?.removeEventListener('dragleave',dragLeave);host.current?.removeEventListener('drop',drop);if(socket){socket.onclose=null;socket.close()}term.dispose();termRef.current=null;searchRef.current=null;focusTerminalInputRef.current=()=>{};claimInputRef.current=()=>{};resizeToPaneRef.current=()=>{} }
  }, [session.id, keybindings, scrollback, rendererPreference, windowsPty, mobileInput])

  const copy = async () => {
    const term = termRef.current
    if (!term?.hasSelection()) { reportError('Copy requires a terminal selection.'); setMenu(null); return }
    const text = term.getSelection()
    // Reported here for the provenance label; the global capture hook would
    // otherwise record the same text as a generic 'app' copy (and is deduped).
    captureCopy(text, 'terminal')
    if (await copyPreparedText(text)) {
      term.clearSelection()
      setPreparedClipboard('')
      setManualClipboard(false)
      showClipboardStatus('Copied')
    } else {
      prepareClipboardFallback(text)
    }
    setMenu(null)
  }
  const paste = async () => {
    const term = termRef.current
    if (term) {
      try {
        await pasteBrowserClipboard(term, session)
        focusTerminalInputRef.current()
        showClipboardStatus('Pasted')
      } catch {
        setManualPaste(true)
        requestAnimationFrame(()=>manualPasteRef.current?.focus())
      }
    }
    setMenu(null)
  }
  const copyLastReply = async () => {
    if(session.backend==='shell')return
    let text=lastReply
    if(!text){
      showClipboardStatus('Loading reply…')
      try {
        const result=await api<{text:string}>('GET',`/api/sessions/${session.id}/last-reply`)
        text=result.text
        setLastReply(text)
      } catch(cause) {
        reportError(cause instanceof Error?cause.message:'No assistant reply is available yet.')
        return
      }
    }
    captureCopy(text,'reply')
    if(await copyPreparedText(text)){
      setPreparedClipboard('');setManualClipboard(false);showClipboardStatus('Reply copied')
    }else prepareClipboardFallback(text)
  }
  const find = () => {
    setFindOpen(true)
    setMenu(null)
  }

  const search = (previous = false) => {
    if (!findQuery) { setFindResult(''); return }
    const found = previous
      ? searchRef.current?.findPrevious(findQuery, { caseSensitive: findCase })
      : searchRef.current?.findNext(findQuery, { caseSensitive: findCase })
    setFindResult(found ? 'match' : 'no match')
  }

  const closeFind = () => {
    setFindOpen(false)
    setFindResult('')
    focusTerminalInputRef.current()
  }

  useEffect(() => {
    const onAction = (event: Event) => {
      const detail = (event as CustomEvent<{sessionId:string|null;action:string;text?:string;submit?:boolean}>).detail
      if (detail.sessionId !== session.id) return
      if (detail.action === 'copy') void copy()
      else if (detail.action === 'paste') void paste()
      else if (detail.action === 'selectAll') { termRef.current?.selectAll(); setMenu(null) }
      else if (detail.action === 'clear') { termRef.current?.clear(); setMenu(null) }
      else if (detail.action === 'find') find()
      else if (detail.action === 'toggleKeyboard') toggleKeyboard()
      else if (detail.action === 'insertText' && detail.text) { injectText(detail.text, detail.submit) }
      // Rail items rendered outside this pane (the drawer's Commands tab) route
      // here rather than touching xterm: the pane stays the single owner of
      // terminal writes, so broadcast, replay, and read/select mode still apply.
      else if (detail.action === 'sendKey' && detail.text) sendKey(detail.text)
      else if (detail.action === 'copyReply') void copyLastReply()
      else if (detail.action === 'copyResume') void copyResumeCommand()
      else if (detail.action === 'branch') onBranch?.()
      else if (detail.action === 'relaunch') runCommand('session.relaunch')
      // Both hosts route to App's `session.kill`, which owns the confirm window and
      // the layout/focus cleanup; the drawer is scoped to the focused session, and a
      // rail click focuses its own pane on pointerdown, so `session.kill` always
      // resolves to the session the button belongs to.
      else if (detail.action === 'endSession') runCommand('session.kill')
      // `captureSelection`/`mux:terminal-selection` (terminal selection into notes) was a
      // half-wired stub — nothing ever dispatched or listened. Deleted with roadmap Phase 4
      // rather than shipped as two incomplete halves of one idea.
    }
    window.addEventListener('mux:terminal-action', onAction)
    return () => window.removeEventListener('mux:terminal-action', onAction)
  }, [session.id, session.backend])


  // Rail key buttons inject raw bytes on the normal onData path (broadcast + replay aware).
  // In read/select mode we deliberately do not refocus, so sending a key never raises the
  // soft keyboard back up.
  // xterm already means to put the viewport back on the tail here (`scrollOnUserInput`), but
  // its own attempt is the single clamped call `scrollTerminalToTail` exists to finish.
  const sendKey=(sequence:string)=>{
    termRef.current?.input(sequence,true)
    scrollTerminalToTail(termRef.current)
    // The rail's own `^End` reaches the same place the chip does, so it takes the chip down
    // with it. Only this one sequence: typing does not move an application's viewport.
    if(sequence===APP_TAIL_KEY&&appOffTailRef.current){appOffTailRef.current=false;setAppOffTail(false)}
    if(!keyboardOffRef.current)focusTerminalInputRef.current()
  }
  // Jump-to-latest, for both viewports rather than only xterm's (see `appOwnsTail`). Scrolling
  // the terminal alone is what left this dead on a phone in a Claude session while the rail's
  // `^End` — which happens to send the same key on its way past — kept working.
  const jumpToLatest=()=>{
    const term=termRef.current
    // A pane that has been forwarding drags counts as much as a live mouse mode: whatever the
    // application has since done with the mouse, it is the thing that scrolled.
    const appReceivesScroll=appOffTailRef.current||term?.modes.mouseTrackingMode!=='none'
    if(term&&appOwnsTail(session.backend,appReceivesScroll))sendViewKeyRef.current(APP_TAIL_KEY)
    scrollTerminalToTail(term)
    appOffTailRef.current=false
    setAppOffTail(false)
    if(!keyboardOffRef.current)focusTerminalInputRef.current()
  }
  const toggleKeyboard=()=>{
    const next=!keyboardOffRef.current
    keyboardOffRef.current=next;setKeyboardOff(next)
    if(next)mobileLiveInputRef.current?.blur()
    else focusTerminalInputRef.current()
  }

  // Clean provider resume command (`claude --resume …` / `codex resume …`) for pasting
  // into a standalone terminal. Null until one exists: shell sessions never have one, and
  // codex only after its rollout file reveals the native session id (see resumeCommand).
  const resumeCmd = resumeCommand(session)
  // Task/Project-Action shells get a leaner rail: a Relaunch button in place of the
  // agent-only Copy reply / Copy resume actions, which have no meaning for them.
  const isTask = !!session.relaunchable
  const copyResumeCommand = async () => {
    if (!resumeCmd) return
    captureCopy(resumeCmd,'resume')
    if (await copyPreparedText(resumeCmd)) {
      setPreparedClipboard('');setManualClipboard(false);showClipboardStatus('Resume command copied')
    } else prepareClipboardFallback(resumeCmd)
  }

  const retryPreparedCopy=async()=>{
    if(!preparedClipboard)return
    if(await copyPreparedText(preparedClipboard,manualClipboardRef.current)){
      setPreparedClipboard('');setManualClipboard(false);showClipboardStatus('Copied');return
    }
    setManualClipboard(true)
    requestAnimationFrame(()=>{manualClipboardRef.current?.focus();manualClipboardRef.current?.select()})
  }

  // Inject literal text (skills, slash commands, custom macros) then optionally
  // submit with Enter, mirroring the raw onData path used by sendKey.
  const injectText=(text:string,submit?:boolean)=>{
    if(!text)return
    const target=termRef.current
    if(target)pasteIntoTerminal(target,session,text)
    if(submit)sendKey('\r')
    else if(!keyboardOffRef.current)focusTerminalInputRef.current()
  }

  // Tap/click feedback for every rail button (voice chips included). Touch
  // browsers keep :hover on the last tapped element, so the CSS hover rule is
  // gated to real pointers and activation instead plays a one-shot pulse that
  // always returns the button to rest. The class is stripped before it is
  // re-added so consecutive taps restart the animation.
  // Driven by click, not pointerdown: the rail scrolls horizontally, so a finger
  // landing on a button is just as likely to be the start of a drag, and pulsing
  // on contact made every swipe look like it had selected whatever it started on.
  // click only fires for a real activation — a scroll drag never produces one.
  const pulseRail=(rail:HTMLElement,target:EventTarget|null)=>{
    const button=target instanceof Element?target.closest('button'):null
    if(!button||button.disabled||!rail.contains(button))return
    button.classList.remove('rail-pulse')
    void button.offsetWidth
    button.classList.add('rail-pulse')
    const clear=()=>{button.classList.remove('rail-pulse');button.removeEventListener('animationend',clear)}
    button.addEventListener('animationend',clear)
  }

  const runPromptItem=async(item:RailItem)=>{
    const note=await activatePromptRailItem(item,{sessionId:session.id,projectId:session.project_id})
    if(note)reportError(note)
  }

  // The rail region after the leading voice chips is data-driven so it can be
  // reordered/extended from settings; built-in ids keep their exact dynamic
  // markup (disabled states, tooltips), generic types render uniformly.
  // Strip placement only: the long tail lives in the utility drawer's Commands
  // tab, where a full-width grid can show it with labels.
  const railItems=resolveRail(loadRailItems(session.project_id),{platform:currentProfile(),backend:session.backend as RailBackend},'strip')
  const renderRailItem=(item:RailItem)=>{
    switch(item.id){
      case 'relaunch':return isTask?<button key={item.id} class="term-relaunch" title="Relaunch this task terminal — stops it and re-runs the same command" onClick={()=>runCommand('session.relaunch')}>Relaunch</button>:null
      // Copy reply / Branch / Paste are icon-only: their marks are conventional enough to read
      // without a word, and dropping four rail-widths of text is what keeps the terminal keys
      // reachable without scrolling. Copy resume deliberately keeps its label — a copy glyph
      // alone cannot distinguish it from Copy reply, and the two sit side by side.
      case 'copyReply':return isTask?null:<button key={item.id} class="rail-icon" disabled={session.backend==='shell'} aria-label="Copy last reply" title={session.backend==='shell'?'Copy reply is available in Claude and Codex sessions':'Copy the latest assistant reply'} onClick={()=>void copyLastReply()}><CopyIcon/></button>
      case 'copyResume':return isTask?null:<button key={item.id} disabled={!resumeCmd} title={resumeCmd?`Copy “${resumeCmd}” to resume this conversation in any terminal${session.backend==='claude'?` (run it from ${session.run_cwd||session.cwd})`:''}`:session.backend==='codex'?'Codex has not reported its session id yet':'Resume commands are available in Claude and Codex sessions'} onClick={()=>void copyResumeCommand()}>Copy resume</button>
      case 'branch':{
        if(!onBranch)return null
        const ready=session.backend==='claude'||(session.backend==='codex'&&!!session.native_session_id&&session.native_session_id!==session.id)
        return <button key={item.id} class="rail-icon" disabled={!ready} aria-label="Branch this conversation" title={ready?'Fork this conversation into a sibling pane, keeping the original open':session.backend==='codex'?'Codex has not reported its session id yet — branch is available shortly':'Branching is available in Claude and Codex sessions'} onClick={()=>onBranch()}><BranchIcon/></button>
      }
      case 'paste':return <button key={item.id} class="rail-icon" aria-label="Paste into terminal" title="Paste the clipboard into this terminal" onClick={()=>void paste()}><PasteIcon/></button>
      case 'clipboardHistory':return <button key={item.id} title="Open clipboard history — recent copies, insertable into this terminal" onClick={()=>runCommand('clipboard.open')}>Clip</button>
      case 'endSession':{
        // Ended sessions keep the button: the same command removes their row from the
        // sidebar, which is the only remaining thing left to do with them.
        const ended=session.state==='exited'||session.state==='crashed'
        const verb=ended?'Remove':'End session'
        return <button key={item.id} class={`rail-danger ${killArmed?'confirming':''}`} aria-label={killArmed?`Confirm ${verb.toLowerCase()}`:verb} title={killArmed?'Click again to confirm':ended?'Remove this ended session from the sidebar (click twice to confirm)':'End this session (click twice to confirm)'} onClick={()=>runCommand('session.kill')}>{killArmed?'Confirm ✓':verb}</button>
      }
      case 'kbdToggle':return <button key={item.id} class={`term-key kbd-toggle ${keyboardOff?'active':''}`} aria-pressed={keyboardOff} title={keyboardOff?'Read mode: tap the terminal to select/scroll without the keyboard. Click to type again.':'Hide the on-screen keyboard so you can select, scroll, and paste'} onClick={toggleKeyboard}>⌨</button>
    }
    if(item.type==='key')return <button key={item.id} class={item.className||'term-key'} title={item.title||item.label} onClick={()=>sendKey(item.bytes||'')}>{item.label}</button>
    if(item.type==='action')return null
    // Prompt templates resolve over the network at click time (see promptRail.ts), so
    // they cannot go through the synchronous payload path below.
    if(item.type==='prompt')return <button key={item.id} class={item.className||''} title={item.title||'Insert this prompt template into the composer'} onClick={()=>void runPromptItem(item)}>{item.label}</button>
    const payload=railPayload(item,session.backend as RailBackend)
    return <button key={item.id} class={item.className||''} title={item.title||payload} onClick={()=>injectText(payload,item.submit)}>{item.label}</button>
  }

  const ownerNotice=inputOwnerNotice(inputOwnership)
  return <div class="terminal-surface"><div class={`terminal-host${letterboxActive?' letterboxed':''}`} ref={host} /><textarea ref={mobileLiveInputRef} class="mobile-terminal-live-input" rows={1} aria-label="Live mobile terminal input" autoCapitalize="off" autoCorrect="off" autoComplete="off" spellcheck={false} inputMode="text" enterkeyhint="enter"/><div class="terminal-action-rail" role="toolbar" aria-label="Terminal keys and clipboard actions" onClick={event=>pulseRail(event.currentTarget,event.target)} onWheel={event=>{const rail=event.currentTarget;if(event.deltaY&&rail.scrollWidth>rail.clientWidth)rail.scrollLeft+=event.deltaY}}>{railItems.map(renderRailItem)}<span aria-live="polite">{clipboardStatus||(selectionText?`${selectionText.length.toLocaleString()} selected${mobileInput.autoCopySelection?' · auto-copy on':''}`:'')}</span>{onConfigureRail&&<button class="rail-config" title="Configure command rail (buttons, order, skills)" aria-label="Configure command rail" onClick={onConfigureRail}>⚙</button>}</div>{(offTail||appOffTail)&&<button class="terminal-jump-latest" title="Scroll to the newest output" aria-label="Jump to latest output" onClick={jumpToLatest}>↓</button>}{imageDropActive&&<div class="terminal-image-drop" role="status">Drop image to attach to {session.backend}</div>}{findOpen && <div class="terminal-find" role="search">
    <input value={findQuery} onInput={event => { setFindQuery(event.currentTarget.value); setFindResult('') }} onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeFind() }
      if (event.key === 'Enter') { event.preventDefault(); search(event.shiftKey) }
    }} placeholder="find in terminal" aria-label="Find in terminal" autofocus />
    <button title="Previous match" onClick={() => search(true)}>↑</button>
    <button title="Next match" onClick={() => search(false)}>↓</button>
    <button class={findCase ? 'active' : ''} title="Match case" aria-pressed={findCase} onClick={() => { setFindCase(value => !value); setFindResult('') }}>Aa</button>
    <span class={findResult === 'no match' ? 'missing' : ''}>{findResult}</span>
    <button title="Close find" onClick={closeFind}>×</button>
  </div>}{ownerNotice
    ?<div class="terminal-input-owner" role="status"><span>{ownerNotice}{letterboxActive?` · showing its ${letterboxSize}`:''}</span><button title="Take terminal input on this device" onClick={()=>{resizeToPaneRef.current();focusTerminalInputRef.current()}}>Take over</button></div>
    /* Letterboxed with nobody else visibly holding input. `inputOwnerNotice` only speaks
       when this pane was *refused*, so the case that looks most broken — a grid drawn at
       a size this pane never chose, with no explanation anywhere — was the one case that
       said nothing at all. Claiming input is the fix as well as the explanation: the owner
       dictates geometry, so taking it resizes the session to this pane. */
    :letterboxActive&&letterboxSettled&&<div class="terminal-input-owner letterbox-notice" role="status"><span>Showing {letterboxSize} · sized elsewhere</span><button title="Size this session to this pane" onClick={()=>{resizeToPaneRef.current();focusTerminalInputRef.current()}}>Resize</button></div>}{connectionState!=='connected'&&<div class={`terminal-connection ${connectionState}`} role="status"><span>{connectionState==='ended'?'session ended':connectionState==='connecting'?'connecting…':'reconnecting…'}</span>{connectionState!=='ended'&&<button class="terminal-connection-retry" title="Reconnect now" onClick={()=>reconnectNowRef.current()}>retry</button>}</div>}{manualPaste&&<div class="manual-terminal-paste" role="dialog" aria-label="Paste into terminal"><span>Clipboard read was blocked. Focus here and use your device’s Paste.</span><textarea ref={manualPasteRef} aria-label="Paste terminal text here" onPaste={event=>{
    const data=event.clipboardData
    const image=data&&clipboardImage(Array.from(data.items))
    if(image){event.preventDefault();void insertTerminalImage(termRef.current!,session,image).then(()=>{setManualPaste(false);showClipboardStatus('Pasted')}).catch(cause=>reportError(cause instanceof Error?cause.message:'Clipboard image paste failed.'));return}
    const text=data?.getData('text/plain')||''
    if(text){event.preventDefault();if(termRef.current)pasteIntoTerminal(termRef.current,session,text);focusTerminalInputRef.current();setManualPaste(false);showClipboardStatus('Pasted')}
  }} onInput={event=>{const text=event.currentTarget.value;if(!text)return;if(termRef.current)pasteIntoTerminal(termRef.current,session,text);event.currentTarget.value='';focusTerminalInputRef.current();setManualPaste(false);showClipboardStatus('Pasted')}}/><button aria-label="Cancel paste" onClick={()=>{setManualPaste(false);focusTerminalInputRef.current()}}>×</button></div>}{preparedClipboard&&<div class="prepared-clipboard" role="status"><span>Clipboard write was blocked. Copy the prepared text once.</span><button onClick={()=>void retryPreparedCopy()}>Copy</button><button aria-label="Dismiss prepared clipboard" onClick={()=>{setPreparedClipboard('');setManualClipboard(false)}}>×</button><textarea ref={manualClipboardRef} class={manualClipboard?'manual':''} readOnly value={preparedClipboard} aria-label="Prepared terminal clipboard text" onFocus={event=>event.currentTarget.select()} /></div>}{menu && <div ref={el=>fitMenuInViewport(el)} class="terminal-menu" role="menu" style={{ left: clampContextMenuLeft(menu.x, innerWidth), top: Math.min(menu.y, innerHeight - 230) }}>
    <button role="menuitem" disabled={!termRef.current?.hasSelection()} onClick={() => runCommand('terminal.copy')}>Copy</button>
    <button role="menuitem" onClick={() => runCommand('terminal.paste')}>Paste</button>
    <button role="menuitem" onClick={() => { setMenu(null); runCommand('clipboard.open') }}>Clipboard history…</button>
    <button role="menuitem" onClick={() => runCommand('terminal.selectAll')}>Select all</button>
    <button role="menuitem" onClick={() => runCommand('terminal.find')}>Find…</button>
    <button role="menuitem" onClick={() => runCommand('terminal.clear')}>Clear display</button>
    <button role="menuitem" onClick={() => runCommand('session.note')}>Session note…</button>
    <button role="menuitem" onClick={() => runCommand('processes.open')}>Processes and previews…</button>
    <div class="context-rule" />
    <button role="menuitem" onClick={() => runCommand('pane.splitHorizontal')}>Split right</button>
    <button role="menuitem" onClick={() => runCommand('pane.splitVertical')}>Split below</button>
    <button role="menuitem" onClick={() => runCommand('pane.detach')}>Detach pane</button>
    <button role="menuitem" onClick={() => runCommand('pane.zoom')}>Zoom pane</button>
    <button role="menuitem" class="danger" onClick={() => runCommand('session.kill')}>{session.state === 'exited' || session.state === 'crashed' ? 'Remove from sidebar' : 'Kill session'}</button>
  </div>}</div>
}

// The terminal body is imperative (xterm owns the DOM), so the component only needs to
// re-render for the handful of session fields its JSX/effects read. Skipping re-renders on
// high-frequency telemetry (context_pct, tokens, model, cwd) avoids diffing every pane and
// the sidebar on every PTY status frame. Callback props (onState/onStartupTiming) are
// intentionally excluded: they are captured by mount-time effects keyed on session.id.
export const TerminalPane = memo(TerminalPaneImpl, (a, b) =>
  a.session.id === b.session.id &&
  a.session.backend === b.session.backend &&
  a.session.state === b.session.state &&
  // Warm panes remain mounted. Swallowing this prop transition leaves the hidden
  // pane registered with the daemon and prevents the shown pane's redraw.
  a.visible === b.visible &&
  // Changes once per agent lifecycle (codex: placeholder → detected rollout id);
  // the resume-command rail button must pick up the flip.
  a.session.native_session_id === b.session.native_session_id &&
  // Task shells set this once at spawn; comparing it keeps the leaner rail authoritative.
  a.session.relaunchable === b.session.relaunchable &&
  a.broadcast === b.broadcast &&
  a.scrollback === b.scrollback &&
  a.keybindings === b.keybindings &&
  // Omitting this blocked a renderer change from ever reaching an existing pane;
  // it only appeared to work because unrelated prop churn re-rendered anyway.
  a.rendererPreference === b.rendererPreference &&
  // Identity-stable per machine (App value-compares it), so this only differs on
  // the single boot transition from "config not loaded yet" to the real value.
  a.windowsPty === b.windowsPty &&
  a.mobileInput === b.mobileInput,
)
