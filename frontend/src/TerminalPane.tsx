import type { ComponentChildren, VNode } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'
import { memo } from 'preact/compat'
import { SettingLink } from './SettingLink'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { SearchAddon } from '@xterm/addon-search'
import { WebLinksAddon } from '@xterm/addon-web-links'
import { WebglAddon } from '@xterm/addon-webgl'
import { Unicode11Addon } from '@xterm/addon-unicode11'
import { ClipboardAddon } from '@xterm/addon-clipboard'
import '@xterm/xterm/css/xterm.css'
import { api, openWebSocket, uploadTerminalAttachment } from './api'
import { takeBranchSeed } from './branchSeed'
import type { Session } from './types'
import { sessionDisplayName } from './sessionNames'
import { applicationTouchScrollProfile, assignsConversationId, harnessDisplayName, isAgentBackend, repaintsScrollback, resolvesTranscriptByCwd, supportsBranch } from './harnessRegistry.ts'
import { keyChord } from './keys'
import { resolvedTheme, terminalThemes, type ThemeName } from './theme'
import { terminalKeyDecision } from './terminalKeys'
import { isTerminalProtocolResponse, shouldSuppressTerminalProtocolResponse } from './terminalProtocol'
import { clampContextMenuLeft, fitMenuInViewport } from './menuPosition'
import {
  applicationTouchScroll,
  mobileDragTarget,
  terminalCellAtPoint,
  terminalScrollSteps,
  terminalSelectionSpan,
  terminalWordRange,
  touchWheelDelta,
  type MobileInputSettings,
  type TerminalCell,
} from './mobileInput'
import { isMobileTerminalInput, mobileEnterNeedsPinnedSend, mobileEnterPayload, mobileImeDelta } from './mobileTerminalIme'
import { claimTerminalTextPaste, clipboardImage, copyPreparedText, pasteNeedsManualBracketing, ResilientClipboardProvider } from './terminalClipboard'
import { noteTerminalFocus } from './insertTarget'
import { captureCopy } from './clipboardHistory'
import { resumeCommand } from './resumeCommand'
import { padSlotKeys, railItemDisplayLabel, railItemDisplayMode, railItemVisible, railPadSlotMode, railPayload, resolveRailRows, type RailBackend, type RailEntry, type RailItem } from './commandRail'
import { isRepeatableRailKey } from './railKeyRepeat'
import { RailRepeatKey, useRailKeyRepeat } from './RailRepeatKey'
import { RailPad, useRailPad, type RailPadSlotView } from './RailPad'
import { activeRailModifiers, applyRailModifiers, consumeRailModifiers, EMPTY_RAIL_MODIFIERS, railModifierForItem, railModifierPhase, railModifierPrefix, toggleRailModifier, type RailModifierState } from './railModifiers'
import { activatePromptRailItem, railItemLabel } from './promptRail'
import { usePromptTitles } from './promptTitles'
import { railItemHasIcon, RailItemIcon, SendIcon } from './railIcons'
import { RailStrip } from './RailStrip'
import { MOBILE_QUERY, currentProfile, loadRailConfig } from './deviceSettings'
import { APP_TAIL_KEY, VIEWPORT_MEASURE_RETRY_FRAMES, VIEWPORT_SETTLE_MS, appOffTailByDistance, appOwnsTail, attachRegistersViewport, createSurfaceRepairScheduler, createViewportScheduler, effectiveViewportCost, inputResetsAppTail, redrawVisibleTerminal, reflowVisibleTerminalRenderer, restoreTerminalScrollAnchor, scrollTerminalToTail, terminalHostIsVisible, terminalRowsAboveTail, terminalSurface, terminalSurfaceChanged, terminalWidthPolicyFontSize, trackAppTailDistance, claudeHostMaxWidth, claudeWidthCap, claudeWidthCapClamping, type SurfaceRepairScheduler, type TerminalSurface } from './terminalViewport'
import { createWheelPacer, isWheelReportBurst } from './terminalWheelPacing'
import { terminalRenderControl } from './terminalRenderPause'
import { adoptsOwnGeometryOnReveal, geometryMatchesFit, letterboxFontSize } from './terminalLetterbox'
import { scaledFontSize } from './uiScale'
import {
  applyOwnerFrame,
  applyOwnerReleased,
  applyRejectedFrame,
  claimReasonForFocus,
  focusHeldByOtherField,
  inputOwnerNotice,
  shouldReclaimAfterDisplacement,
  shouldReplayRejectedInput,
  UNOWNED,
  type ClaimReason,
  type FocusedField,
  type OwnershipView,
} from './inputOwnership'
import { pendingInputDecision } from './pendingInput'
import { bracketedPaste, composerInsertionParts } from './composerInsertion'
import { deviceIsFocused, PRESENCE_REPORTED_EVENT } from './devicePresence'
import {
  attachmentNeedsManualBracketing,
  attachmentReferenceText,
  attachmentSafeBroadcast,
  canInsertTerminalAttachment,
  MAX_ATTACHMENTS_PER_ACTION,
  type UploadedTerminalAttachment,
} from './terminalAttachments'
import { localPreviewUrl } from './previewLinks'
import { insertionRefusal, settleTerminalInsertion } from './terminalActions.ts'
import { HANDSHAKE_TIMEOUT_MS, retryDelay, terminalAttachAllowed, watchLiveness, type ConnectionPhase } from './liveness'
import {
  shouldLoadWebgl,
  terminalAttachReadyFrame,
  terminalCursorOptions,
  type ActiveTerminalRenderer,
  type TerminalRendererPreference,
  type WindowsPtyCompatibility,
} from './terminalRenderer'
import {
  isWebglRenderError,
  recordTerminalRenderDiagnostic,
  terminalRenderDiagnosticsEnabled,
} from './terminalRenderDiagnostics'
import { remountDecision, scrollbackRepaintNeeded, surfaceDrifted, terminalFitDrifted, TERMINAL_HEALTH_SWEEP_MS, writePipelineBacklogged, writePipelineStalled } from './terminalHealth'
import {
  INPUT_LATENCY_REPORT_MS,
  TerminalInputLatencyTracker,
  inputEventPerformanceTime,
  watchMainThreadStalls,
  type TerminalInputCapture,
  type TerminalInputSource,
} from './terminalInputDiagnostics'
import { reportPromptSubmitted } from './projectRecency'
import { SOFT_KEYBOARD_EVENT, clampPeekOffset, hiddenOutputDeservesPeek, holdSoftKeyboard, lastSoftKeyboardInset, nextPeekOffset, peekToggleVisible, restoreSoftKeyboard, softKeyboardDismissals, softKeyboardHolder, softKeyboardInputMode, type PeekTrigger } from './mobileKeyboard'
import { dismissStack } from './dismissStack.ts'
import { useDismissLevel } from './modalFocus'
import { RESERVE_INTENT_WINDOW_MS, nextReserveState, paintedRowCount, reservedKeyboardPx } from './keyboardReserve'
import { MobileTerminalDraft } from './TerminalDraftComposer'
import {
  insertMobileTerminalDraft,
  mobileTerminalDraftStore,
  mobileTerminalInputMode,
  nextMobileTerminalInputMode,
  type MobileTerminalInputMode,
} from './mobileTerminalDraft'
import {
  caretResolverForBackend,
  caretSteerCommand,
  dispatchTerminalMouseTap,
  resolveAnchoredCaretTarget,
  terminalCaretAtPoint,
  terminalTapAction,
  type CaretPointerType,
  type TerminalCaretPosition,
  type TerminalCaretSnapshot,
} from './terminalCaretPlacement'
import { composerIsReadable, readComposerText } from './composerText'
import { ClipboardDropup } from './ClipboardDropup'
import { SkillsDropup } from './SkillsDropup'
import { PromptsDropup } from './PromptsDropup'

type StartupMilestone = 'pane_mounted' | 'socket_open' | 'replay_ready'

/** Which rail drop-up is open, and the button it hangs from. */
type RailDropupState = { kind: 'clipboard' | 'skills' | 'prompts'; anchor: HTMLElement }

/**
 * The pane's normal font size at 100% chrome scale. A letterboxed pane renders below
 * whatever this resolves to, and never above it.
 *
 * Multiplied by the user's UI scale, so terminal type moves with the rest of the
 * interface instead of staying put while everything around it grows. That does change
 * the grid this pane proposes — a bigger font fits fewer columns and rows in the same
 * box — and that proposal is what cross-device arbitration reduces. Intended: it is no
 * different from resizing the window, and the alternative is a device rendering a grid
 * whose type its owner asked to be able to read.
 */
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
/** How long output must stop for before the keyboard-layout questions are re-asked. */
const KEYBOARD_LAYOUT_SETTLE_MS = 250
/**
 * How recently the reader must have typed for output in the hidden half of the grid to be
 * left alone. Typing means they are composing rather than waiting for an answer, and a pane
 * that jumps to the top mid-sentence is worse than one that never moves.
 */
const HIDDEN_OUTPUT_INPUT_GRACE_MS = 1500
/**
 * Vertical travel below which a drag that moved nothing is not worth reporting.
 *
 * A finger resting on the glass delivers a few pixels of jitter per touch event, and a
 * tap that wobbles is not a reader complaining that scrolling is broken.
 */
const MOBILE_DRAG_INERT_MIN_TRAVEL_PX = 40
/**
 * Whether a soft keyboard is what this device types with. `MOBILE_QUERY` is a width, and a
 * desktop window dragged narrow matches it — reserving 40% of that pane for a keyboard that
 * is never coming would be a bug with no symptom the user could connect to anything.
 */
const typesWithSoftKeyboard = () => !!window.matchMedia?.('(pointer:coarse)').matches

/**
 * How long the Claude width-cap notice stays up after a resize that the cap clamped.
 *
 * The opposite problem to the letterbox notice above, and so the opposite treatment.
 * A letterbox that resolves is not worth announcing, so that one waits; a clamped
 * width never resolves on its own, so this one appears immediately and leaves on a
 * timer instead. Long enough to read a sentence and reach the button, short enough
 * that a user who keeps a wide layout is not told about it forever.
 */
const WIDTH_CAP_NOTICE_MS = 6000

/**
 * Rows one gesture event may forward to an application that holds the mouse.
 *
 * A sanity bound, not a scroll budget: a full-screen flick asks for a dozen rows at most,
 * while a pane measured mid-relayout can report a row height near zero and turn the same
 * finger travel into thousands. The wheel pacer bounds the *rate* those reports leave at;
 * this bounds what a single arithmetic accident can put into it.
 */
const APPLICATION_SCROLL_MAX_ROWS = 64

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
   * The chrome scale, applied to this pane's font. Deliberately not in the terminal's
   * construction effect: font size is live-assignable, and rebuilding the terminal (as
   * `scrollback` has to) would drop the socket and replay the whole buffer to change a
   * number xterm will take directly.
   */
  uiScale: number
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
  /**
   * The configured Claude width envelope in columns, 0 for none. Applied here rather
   * than in the fit because the cap is a property of the *box*: capping the host and
   * letting FitAddon measure the clamped result keeps xterm, the grid the daemon is
   * told about, and the pixels on screen derived from one number, where a capped
   * column count would leave the host wider than the terminal drawn inside it.
   */
  claudeMaxColumns: number
  /** Open Configure Actions from the rail's trailing gear. */
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

function acceptsTerminalAttachments(session: Session) {
  return isAgentBackend(session.backend)
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
 *
 * The paste wrapper does not protect a payload's FIRST newline, which is a separate
 * measured defect and is why the leading run is lifted out into key presses ahead of
 * the paste (`composerInsertion.ts`). It goes through `term.input` rather than being
 * prepended to the paste text, because xterm would rewrite it to a bare CR.
 */
function pasteIntoTerminal(term: Terminal, session: Session, text: string) {
  const { leading, body } = composerInsertionParts(text, session.backend)
  if (leading) term.input(leading, true)
  if (!body) return
  if (pasteNeedsManualBracketing({
    text: body,
    agentBackend: acceptsTerminalAttachments(session),
    bracketedPasteMode: term.modes.bracketedPasteMode,
  })) {
    // term.input keeps this on the normal onData path, so broadcast membership and
    // the replay guard treat it exactly like a real paste.
    term.input(bracketedPaste(body), true)
    return
  }
  term.paste(body)
}

function terminalCaretSnapshot(term: Terminal): TerminalCaretSnapshot {
  const buffer = term.buffer.active
  const lines = []
  for (let row = buffer.viewportY; row < buffer.viewportY + term.rows; row += 1) {
    const line = buffer.getLine(row)
    const cells = []
    for (let column = 0; column < term.cols; column += 1) {
      const cell = line?.getCell(column)
      cells.push({
        chars: cell?.getChars() ?? '',
        code: cell?.getCode() ?? 0,
        width: cell?.getWidth() ?? 1,
        bgMode: cell?.getBgColorMode() ?? 0,
        bg: cell?.getBgColor() ?? 0,
        dim: cell?.isDim() === 1,
      })
    }
    lines.push({ row, cells })
  }
  return {
    cols: term.cols,
    rows: term.rows,
    viewportY: buffer.viewportY,
    baseY: buffer.baseY,
    cursorX: buffer.cursorX,
    cursorY: buffer.cursorY,
    lines,
  }
}

function mobileClipboardFallback(): boolean {
  return window.matchMedia('(max-width: 760px), (pointer: coarse)').matches
}

/** The focused element, in the shape `focusHeldByOtherField` reads. `.xterm` is what
 *  marks a terminal's own hidden textarea, so a pane still takes focus from another
 *  terminal - only a text field elsewhere in the app holds it off. */
function activeEditableField(): FocusedField {
  const active = document.activeElement
  if (!(active instanceof HTMLElement)) return null
  return { tagName: active.tagName, isContentEditable: active.isContentEditable, inTerminal: !!active.closest('.xterm') }
}

async function pasteBrowserClipboard(term: Terminal, session: Session, attach: (files: Blob[]) => Promise<void>): Promise<'attachment'|'text'> {
  if (acceptsTerminalAttachments(session) && typeof navigator.clipboard.read === 'function') {
    try {
      const items = await navigator.clipboard.read()
      const item = items.find(candidate => candidate.types.some(type => type.startsWith('image/')))
      const mediaType = item?.types.find(type => type.startsWith('image/'))
      if (item && mediaType) {
        await attach([await item.getType(mediaType)])
        return 'attachment'
      }
    } catch {
      // Some browsers block the richer Clipboard API while still allowing text reads.
    }
  }
  pasteIntoTerminal(term, session, await navigator.clipboard.readText())
  return 'text'
}

function TerminalPaneImpl({ session, onState, onStartupTiming, startupOrigin, broadcast, keybindings, scrollback, rendererPreference, windowsPty, mobileInput, uiScale, visible, claudeMaxColumns, onConfigureRail, onBranch }: Props) {
  const host = useRef<HTMLDivElement>(null)
  // Held in a ref rather than closed over: every reader below lives inside the
  // terminal's construction effect, which must not re-run just because a font moved.
  const baseFont = scaledFontSize(BASE_FONT_SIZE, uiScale)
  const baseFontRef = useRef(baseFont)
  baseFontRef.current = baseFont
  const applyBaseFontRef = useRef<() => void>(() => {})
  const termRef = useRef<Terminal | null>(null)
  const searchRef = useRef<SearchAddon | null>(null)
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)
  const [dropup, setDropup] = useState<RailDropupState | null>(null)
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
  const [fileDropActive,setFileDropActive]=useState(false)
  const [attachmentBusy,setAttachmentBusy]=useState(false)
  const [attachmentReady,setAttachmentReady]=useState(false)
  const [approveBusy,setApproveBusy]=useState(false)
  // True while the viewport sits above the newest line. Mirrored in a ref so the
  // per-render tail check can bail without touching component state.
  const [offTail,setOffTail]=useState(false)
  const offTailRef=useRef(false)
  // The same question about the *application's* viewport, which xterm cannot answer. A TUI
  // holding the mouse (Claude) receives a phone's drags as wheel events and scrolls itself,
  // so xterm's buffer never leaves its tail and `offTail` above stays false forever - which
  // is why the chip has only ever appeared in Codex sessions. Nothing reports that scroll
  // back, so the pane totals the scroll it forwards and reads the total as the distance from
  // that viewport's newest line (`trackAppTailDistance`), in the pixels the wheel events
  // carry. Dragging back down spends the same total, which is what takes the chip away for a
  // reader who scrolled their own way back rather than tapping it.
  const [appOffTail,setAppOffTail]=useState(false)
  const appOffTailRef=useRef(false)
  const appTailDistanceRef=useRef(0)
  // Forget that estimate: the application's viewport is on its newest line again, or the
  // viewport the estimate described no longer exists. `reason` is recorded because the
  // estimate is the one part of the chip nothing on screen can be checked against - a chip
  // that outstays the scroll it was raised for, or leaves before it, is only diagnosable
  // from the sequence of distances and the event that reset them.
  const clearAppTail=(reason:string)=>{
    const distance=appTailDistanceRef.current
    appTailDistanceRef.current=0
    if(!appOffTailRef.current)return
    appOffTailRef.current=false
    setAppOffTail(false)
    recordTerminalRenderDiagnostic(session.id,'app_tail_cleared',{reason,distance})
  }
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
  // Whether this device is on the compact projection, tracked live because a desktop
  // window dragged across the breakpoint has to drop the desktop width envelope with
  // the rest of the desktop layout. Same query the workspace and the settings
  // profiles use, so all three flip together.
  const [compactLayout,setCompactLayout]=useState(()=>
    typeof window!=='undefined'&&!!window.matchMedia?.(MOBILE_QUERY).matches)
  useEffect(()=>{
    const query=window.matchMedia(MOBILE_QUERY)
    const on=()=>setCompactLayout(query.matches)
    on()
    query.addEventListener('change',on)
    return()=>query.removeEventListener('change',on)
  },[])
  const compactLayoutRef=useRef(compactLayout)
  compactLayoutRef.current=compactLayout
  const widthCap=claudeWidthCap(session.backend,compactLayout,claudeMaxColumns)
  // The cap is invisible from inside the terminal. The grid is correct, the pane is
  // simply narrower than the box holding it, and every symptom of that - a drag that
  // stops widening the text, margin appearing on both sides - reads as the CLI
  // refusing to resize rather than as this app declining to ask it to. So the pane
  // says so, at the drag that raises the question and only then: the notice goes up
  // whenever a width change comes back clamped, and back down a few seconds later,
  // which keeps an explanation from becoming furniture on a layout the user has
  // already accepted. Holds the cap it is describing, or 0 for hidden.
  const [widthCapNotice,setWidthCapNotice]=useState(0)
  const widthCapNoticeTimer=useRef(0)
  const showWidthCapNotice=useRef<(cap:number)=>void>(()=>{})
  showWidthCapNotice.current=(cap:number)=>{
    setWidthCapNotice(cap)
    window.clearTimeout(widthCapNoticeTimer.current)
    widthCapNoticeTimer.current=window.setTimeout(()=>setWidthCapNotice(0),WIDTH_CAP_NOTICE_MS)
  }
  const hideWidthCapNotice=useRef<()=>void>(()=>{})
  hideWidthCapNotice.current=()=>{
    window.clearTimeout(widthCapNoticeTimer.current)
    setWidthCapNotice(0)
  }
  useEffect(()=>()=>window.clearTimeout(widthCapNoticeTimer.current),[])
  // Read from inside the terminal's construction effect, which must not re-run when a
  // setting moves - the pane would drop its socket and replay the whole buffer to
  // change a max-width.
  const widthCapRef=useRef(widthCap)
  widthCapRef.current=widthCap
  // Bumped when another surface edits the shared rail config so this pane re-reads it.
  const [,bumpRailRev]=useState(0)
  useEffect(()=>{const on=()=>bumpRailRev(value=>value+1);window.addEventListener('mux:settings-changed',on);return()=>window.removeEventListener('mux:settings-changed',on)},[])
  // Ending a session is a two-click confirm, and App owns both the armed id and the
  // window that disarms it (`requestKill`). The rail button mirrors that broadcast
  // rather than running a second timer of its own, so its label can never disagree
  // with what the next click will actually do.
  const [killArmed,setKillArmed]=useState(false)
  // Draft text belongs to the session, not this pane instance. The pane is reused across
  // stack tabs and mobile browsers may suspend it, so the bounded device-local registry is
  // authoritative while this state is only the visible editing copy.
  const [mobileDraftOpen,setMobileDraftOpen]=useState(false)
  const mobileDraftOpenRef=useRef(false)
  const [mobileDraftText,setMobileDraftTextState]=useState(()=>mobileTerminalDraftStore.get(session.id))
  const mobileDraftTextRef=useRef(mobileDraftText)
  const [mobileDraftInserting,setMobileDraftInserting]=useState(false)
  const mobileDraftInsertingRef=useRef(false)
  const mobileDraftInsertGenerationRef=useRef(0)
  const [mobileDraftError,setMobileDraftError]=useState('')
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
    setFileDropActive(false)
    setAttachmentBusy(false)
    setAttachmentReady(false)
    setFindOpen(false)
    setFindQuery('')
    setFindResult('')
    setKillArmed(false)
    mobileDraftOpenRef.current=false
    setMobileDraftOpen(false)
    const savedDraft=mobileTerminalDraftStore.get(session.id)
    mobileDraftTextRef.current=savedDraft
    setMobileDraftTextState(savedDraft)
    mobileDraftInsertGenerationRef.current+=1
    mobileDraftInsertingRef.current=false
    setMobileDraftInserting(false)
    setMobileDraftError('')
    setInputOwnership(UNOWNED)
    setLetterboxSize('')
    // The pane instance is reused across tab switches, so a notice raised for a Claude
    // session would otherwise sit over the Codex session that replaced it.
    hideWidthCapNotice.current()
    clearAppTail('session_switch')
  },[session.id])
  useEffect(()=>{
    const on=(event:Event)=>setKillArmed((event as CustomEvent<string|null>).detail===session.id)
    window.addEventListener('mux:kill-armed',on)
    return()=>window.removeEventListener('mux:kill-armed',on)
  },[session.id])
  const manualClipboardRef=useRef<HTMLTextAreaElement>(null)
  const manualPasteRef=useRef<HTMLTextAreaElement>(null)
  const attachmentInputRef=useRef<HTMLInputElement>(null)
  const mobileLiveInputRef=useRef<HTMLTextAreaElement>(null)
  const focusTerminalInputRef=useRef<()=>void>(()=>{})
  const focusAfterTerminalActionRef=useRef<()=>void>(()=>{})
  const pasteAttachmentRef=useRef<(text:string,nativeImage:boolean)=>void>(()=>{})
  const attachFilesRef=useRef<(files:Blob[])=>Promise<void>>(async()=>{})
  const attachmentBusyRef=useRef(false)
  const attachmentReadyRef=useRef(false)
  const attachmentOperationRef=useRef(0)
  const activeSessionIdRef=useRef(session.id)
  activeSessionIdRef.current=session.id
  useEffect(()=>{
    attachmentOperationRef.current+=1
    attachmentBusyRef.current=false
    attachmentReadyRef.current=false
  },[session.id])
  // Read/select mode: when on (touch only), tapping the terminal no longer raises the soft
  // keyboard, so you can select, scroll, and paste without it. The ref is what the pointer
  // and focus closures created in the mount effect read at call time.
  const keyboardOffRef=useRef(false)
  const [keyboardOff,setKeyboardOff]=useState(false)
  // How much of this pane the soft keyboard is covering, and whether the reader has asked
  // to look at the top of the grid instead of the composer. Only meaningful while the
  // keyboard is up: with it down the whole grid fits and there is no slice to move.
  const [keyboardInset,setKeyboardInset]=useState(0)
  const keyboardInsetRef=useRef(0)
  // Android Back hides the IME without blurring its textarea. Track whether focus still
  // represents typing intent separately, so a later rail action can restore focus with
  // `inputmode="none"` instead of treating stale focus as a request to reopen the keyboard.
  const mobileTypingIntentRef=useRef(false)
  // Whether this pane is holding the keyboard's height back out of its own grid, so the
  // keyboard covers dead space instead of conversation (see keyboardReserve.ts). A reserved
  // pane has nothing hidden to peek at, which is why every peek path below reads
  // `effectiveKeyboardInset` rather than the raw inset.
  const [keyboardReserved,setKeyboardReserved]=useState(false)
  const keyboardReservedRef=useRef(false)
  keyboardReservedRef.current=keyboardReserved
  // The pixels held back while reserved, and when that last changed. Refs rather than
  // state because the evaluator runs off a timer inside the mount effect and its own
  // previous answer is an input to the next one.
  const reservePxRef=useRef(0)
  const reserveChangedAtRef=useRef(0)
  // When the reader last moved to type. A reservation is only held while a keyboard is up
  // or one is on its way, and this is the "on its way" half.
  const typingIntentAtRef=useRef(0)
  // Set inside the mount effect. The keyboard opening or closing changes both keyboard-layout
  // answers without producing a single byte of output, so the event has to ask for the pass
  // that output would otherwise have scheduled.
  const scheduleKeyboardSettleRef=useRef<()=>void>(()=>{})
  const effectiveKeyboardInset=keyboardReserved?0:keyboardInset
  // How far the grid is pushed back down inside the slid surface: 0 is the composer, the
  // full inset is the top of the grid, and a drag parks it anywhere between. Held in a ref
  // as well as state because a touch-move writes it at pointer rate and must not wait for
  // a render to read back what it just set.
  const [peekOffset,setPeekOffset]=useState(0)
  const peekOffsetRef=useRef(0)
  const effectiveKeyboardInsetRef=useRef(0)
  effectiveKeyboardInsetRef.current=effectiveKeyboardInset
  // A drag moves the grid every frame; a button press or an auto-move is a jump. Only the
  // jump gets an animation, because a transition on a dragged offset lags the finger.
  const [peekAnimated,setPeekAnimated]=useState(false)
  // Set inside the mount effect: writes the offset straight to the surface element. A drag
  // produces one of these per pointer move, and re-rendering a terminal pane at pointer rate
  // is a frame budget this pane does not have, so state catches up when the gesture ends
  // (`commitPeekOffset`) rather than on every move.
  const paintPeekOffsetRef=useRef<(offset:number)=>void>(()=>{})
  const setPeekOffsetValue=(next:number,animated:boolean)=>{
    if(next===peekOffsetRef.current&&animated===peekAnimated)return
    peekOffsetRef.current=next
    if(animated){setPeekOffset(next);setPeekAnimated(true);return}
    paintPeekOffsetRef.current(next)
  }
  const commitPeekOffset=()=>{
    if(peekOffsetRef.current===peekOffset&&!peekAnimated)return
    setPeekOffset(peekOffsetRef.current)
    setPeekAnimated(false)
  }
  const commitPeekOffsetRef=useRef(commitPeekOffset)
  commitPeekOffsetRef.current=commitPeekOffset
  const setPeekOffsetValueRef=useRef(setPeekOffsetValue)
  setPeekOffsetValueRef.current=setPeekOffsetValue
  const applyPeek=(trigger:PeekTrigger)=>{
    setPeekOffsetValueRef.current(
      nextPeekOffset(peekOffsetRef.current,trigger,effectiveKeyboardInsetRef.current),
      true,
    )
  }
  const applyPeekRef=useRef(applyPeek)
  applyPeekRef.current=applyPeek
  useEffect(()=>{
    const onKeyboard=(event:Event)=>{
      const inset=(event as CustomEvent<number>).detail
      keyboardInsetRef.current=inset
      setKeyboardInset(inset)
      const bridge=mobileLiveInputRef.current
      if(inset<=0){
        mobileTypingIntentRef.current=false
        // A keyboard that has gone outranks a stale intent to raise one, so the pane gives
        // its reserved strip back now rather than at the end of the intent window.
        typingIntentAtRef.current=0
        if(bridge&&typesWithSoftKeyboard())bridge.inputMode='none'
      }else if(bridge&&!keyboardOffRef.current&&!mobileDraftOpenRef.current){
        mobileTypingIntentRef.current=true
        bridge.inputMode='text'
      }
      if(inset<=0)applyPeekRef.current('keyboardClosed')
      scheduleKeyboardSettleRef.current()
    }
    window.addEventListener(SOFT_KEYBOARD_EVENT,onKeyboard)
    return()=>window.removeEventListener(SOFT_KEYBOARD_EVENT,onKeyboard)
  },[])
  // A pane that reserved space while peeked would leave the grid pushed down past a
  // keyboard that now covers nothing. Reserving and peeking are alternatives, not layers.
  useEffect(()=>{
    if(keyboardReserved&&peekOffsetRef.current!==0)setPeekOffsetValueRef.current(0,true)
  },[keyboardReserved])
  // Set inside the mount effect; lets a layout change outside the ResizeObserver's reach
  // (the voice strip appearing/vanishing under the terminal) force an xterm re-fit.
  const scheduleFitRef=useRef<()=>void>(()=>{})
  // Last-resort self-repair: a detected-dead write pipeline bumps this epoch, which
  // re-runs the mount effect — a fresh Terminal, socket, and replay. Bounded by
  // `remountDecision` so a poison sequence in the retained buffer (replayed on every
  // rebuild) degrades into one visible error rather than a remount loop.
  const [remountEpoch,setRemountEpoch]=useState(0)
  const remountAttemptsRef=useRef<number[]>([])
  const requestPaneRemount=()=>{
    const decision=remountDecision(remountAttemptsRef.current,Date.now())
    remountAttemptsRef.current=decision.attempts
    if(!decision.allow){
      reportError('The terminal renderer failed repeatedly for this session; its output may be stale until you reopen the pane.')
      return
    }
    reportError('The terminal renderer stalled; rebuilding this pane.')
    setRemountEpoch(epoch=>epoch+1)
  }
  const requestPaneRemountRef=useRef(requestPaneRemount)
  requestPaneRemountRef.current=requestPaneRemount
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
  // An ended or recovered pane has no child to receive keystrokes. The daemon
  // refuses the write either way, so this is not what makes it safe — it is what
  // stops a pane the operator is reading from filling the socket with input it
  // will only be told about in a refusal, and from claiming the keyboard away
  // from a live pane on another device while doing it.
  const readOnlyRef=useRef(false)
  readOnlyRef.current=session.state==='exited'||session.state==='crashed'
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

  attachFilesRef.current=async(files:Blob[])=>{
    if(!acceptsTerminalAttachments(session)){
      reportError('Attach files to an open agent session.')
      return
    }
    if(['exited','crashed'].includes(session.state)){
      reportError('Files cannot be attached to an ended session.')
      return
    }
    if(!canInsertTerminalAttachment(session.state,attachmentReadyRef.current)||!termRef.current){
      reportError('The terminal is still restoring. Wait for it to reconnect and try again.')
      return
    }
    if(!files.length)return
    if(files.length>MAX_ATTACHMENTS_PER_ACTION){
      reportError(`Attach at most ${MAX_ATTACHMENTS_PER_ACTION} files at once.`)
      return
    }
    if(attachmentBusyRef.current){
      reportError('Another attachment upload is still running.')
      return
    }
    const operation=++attachmentOperationRef.current
    const startedSession=session.id
    attachmentBusyRef.current=true
    setAttachmentBusy(true)
    const uploaded:UploadedTerminalAttachment[]=[]
    const failures:string[]=[]
    try{
      for(let index=0;index<files.length;index+=1){
        const file=files[index] as File
        const subtype=file.type.split('/',2)[1]||'bin'
        const filename=file.name?.trim()||`clipboard.${subtype}`
        showClipboardStatus(`Attaching ${index+1}/${files.length}…`)
        const form=new FormData()
        form.append('file',file,filename)
        try{
          uploaded.push(await uploadTerminalAttachment<UploadedTerminalAttachment>(`/api/sessions/${startedSession}/attachments`,form))
        }catch(cause){
          failures.push(cause instanceof Error?cause.message:`${filename} failed`)
        }
        if(activeSessionIdRef.current!==startedSession||attachmentOperationRef.current!==operation)return
      }
      const pathReferences:string[]=[]
      for(const item of uploaded){
        if(item.kind==='image')pasteAttachmentRef.current(item.reference,true)
        else pathReferences.push(item.path)
      }
      const pathText=attachmentReferenceText(pathReferences)
      if(pathText)pasteAttachmentRef.current(pathText,false)
      if(uploaded.length)showClipboardStatus(`${uploaded.length} file${uploaded.length===1?'':'s'} attached`)
      if(failures.length)reportError(`${failures.length} attachment${failures.length===1?'':'s'} failed: ${failures.at(-1)}`)
    }finally{
      if(attachmentOperationRef.current===operation){
        attachmentBusyRef.current=false
        setAttachmentBusy(false)
      }
    }
  }

  useEffect(() => () => {
    if(clipboardStatusTimerRef.current!==null)window.clearTimeout(clipboardStatusTimerRef.current)
  }, [])

  // A drop-up holds a direct reference to its trigger, so a session switch closes it before
  // the old rail unmounts and leaves the overlay anchored to a detached node.
  useEffect(() => { setDropup(null) }, [session.id])

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
    const mobileTerminalInput = isMobileTerminalInput()
    const term = new Terminal({
      cursorBlink: true, cursorStyle: 'bar', fontFamily: '"Cascadia Mono", Consolas, monospace',
      ...terminalCursorOptions(mobileTerminalInput),
      fontSize: baseFontRef.current, fontWeight: '600', fontWeightBold: '600', lineHeight: 1.2, scrollback, allowProposedApi: true,
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
    const openUri=(event:MouseEvent,uri:string)=>{
      const previewUrl=localPreviewUrl(uri)
      if(previewUrl){
        event.preventDefault()
        window.dispatchEvent(new CustomEvent('mux:open-terminal-preview',{detail:{sessionId:session.id,url:previewUrl}}))
        return
      }
      window.open(uri,'_blank','noopener,noreferrer')
    }
    term.loadAddon(new WebLinksAddon(openUri))
    // WebLinksAddon only matches URLs that appear as literal text. An OSC 8
    // hyperlink carries its destination out of band and renders as a label, so a
    // loopback server announced that way had no clickable route to a Preview at
    // all — which is exactly how a Codex TUI renders `[label](http://127.0.0.1:…)`.
    // Both link kinds now resolve through the same handler.
    term.options.linkHandler={activate:openUri}
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
    // Warm panes keep parsing but must not keep rendering: see terminalRenderPause.ts.
    // Created once — renderer swaps (WebGL load, DOM fallback) replace the renderer
    // inside the same RenderService, so the control stays valid across them.
    const renderControl = terminalRenderControl(term)
    let normalFontSize = baseFontRef.current
    const proposeNormalDimensions = (restoreFont: boolean) => {
      const previousFont = term.options.fontSize ?? baseFontRef.current
      const base = baseFontRef.current
      term.options.fontSize = base
      let proposed = fit.proposeDimensions()
      if (proposed && Number.isFinite(proposed.cols) && Number.isFinite(proposed.rows)) {
        normalFontSize = terminalWidthPolicyFontSize(
          session.backend,
          window.matchMedia('(max-width:760px)').matches,
          proposed.cols,
          base,
        )
        if (normalFontSize !== base) {
          term.options.fontSize = normalFontSize
          proposed = fit.proposeDimensions()
        }
      }
      if (restoreFont) term.options.fontSize = previousFont
      return proposed && Number.isFinite(proposed.cols) && Number.isFinite(proposed.rows)
        ? proposed
        : undefined
    }
    const fitVisiblePane = () => {
      if (!terminalHostIsVisible(host.current)) return false
      const proposed = proposeNormalDimensions(false)
      if (!proposed) return false
      if (term.cols !== proposed.cols || term.rows !== proposed.rows) {
        term.resize(proposed.cols, proposed.rows)
      }
      return true
    }
    const mobileLiveInput=mobileTerminalInput?mobileLiveInputRef.current:null
    // The live-input textarea is uncontrolled, and switching stack tabs re-runs this
    // effect against the *same* DOM node (the pane is rendered unkeyed as the stack's
    // only active child, so Preact reuses the instance). The IME delta baseline below
    // starts empty on every run, so any text left in the element would be re-sent in
    // full on the next keystroke — duplicating the composer contents, or leaking the
    // previous tab's text into this session. Element and baseline must start in sync.
    if(mobileLiveInput){
      mobileLiveInput.value=''
      mobileLiveInput.inputMode=softKeyboardInputMode(typesWithSoftKeyboard(),keyboardInsetRef.current,mobileTypingIntentRef.current)
    }
    let mobileCursorInitialized=false
    const focusTerminalInput=(typingIntent=true)=>{
      if(keyboardOffRef.current||mobileDraftOpenRef.current)return
      if(mobileLiveInput){
        mobileTypingIntentRef.current=typingIntent
        // The earliest honest signal that a keyboard is coming. The reservation has to be
        // in place before it finishes animating in, or the grid it was meant to pre-size
        // is already full and the shrink is refused.
        if(typingIntent){
          typingIntentAtRef.current=Date.now()
          scheduleKeyboardSettleRef.current()
        }
        mobileLiveInput.inputMode=softKeyboardInputMode(typesWithSoftKeyboard(),keyboardInsetRef.current,typingIntent)
        // xterm does not render any cursor until its own textarea has received focus
        // once. Claude normally initializes it by entering the alternate screen, but
        // Codex stays on the normal screen. Briefly focus xterm on the first user
        // focus, then hand focus to the native IME bridge that actually receives input.
        if(typingIntent&&!mobileCursorInitialized){term.focus();mobileCursorInitialized=true}
        mobileLiveInput.focus({preventScroll:true})
        const end=mobileLiveInput.value.length
        mobileLiveInput.setSelectionRange(end,end)
      }else term.focus()
    }
    focusTerminalInputRef.current=()=>focusTerminalInput(true)
    // Synthetic terminal actions preserve the keyboard state they found. Refocusing with
    // `inputmode="none"` is intentional: unlike a blur-only fix it keeps physical-keyboard
    // input routed to the terminal and also covers Android Back's focused-but-hidden IME.
    focusAfterTerminalActionRef.current=()=>focusTerminalInput(mobileTypingIntentRef.current||keyboardInsetRef.current>0)
    // Attachment paths are always unicast. xterm fires onData synchronously from
    // paste/input, so this depth marker reaches the ordinary input path without
    // bypassing its replay guard or bracketed-paste handling.
    let attachmentPasteDepth=0
    // Cursor steering and synthesized touch mouse reports also stay on xterm's normal
    // onData path. Their depth markers make them unicast and distinguish them from a
    // real keystroke, which cancels an in-flight placement immediately.
    let caretPlacementInputDepth=0
    let syntheticMouseInputDepth=0
    let cancelCaretPlacement=()=>{}
    pasteAttachmentRef.current=(text,nativeImage)=>{
      attachmentPasteDepth+=1
      try{
        if(attachmentNeedsManualBracketing(nativeImage,acceptsTerminalAttachments(session),term.modes.bracketedPasteMode))term.input(bracketedPaste(text),true)
        else if(nativeImage)term.paste(text)
        else pasteIntoTerminal(term,session,text)
      }finally{
        attachmentPasteDepth-=1
      }
      focusAfterTerminalActionRef.current()
    }
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
          if (copied) term.clearSelection()
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
    // The daemon ring position this pane has parsed up to: the anchor from the last
    // `replay_end` plus every live byte since. Offered as `since` on the next attach so
    // a reconnect appends only what was missed instead of resetting the terminal —
    // which is what used to truncate a pane to one replay window's worth of scrollback
    // on every tab switch or minimize. Exact because live binary frames are the ring's
    // own bytes in order; a `gap` frame (dropped chunks) breaks the count, so it nulls
    // the cursor until the resync's `replay_end` re-anchors it.
    let serverPosition: number | null = null
    // One transcript restatement per parsed buffer: set when this pane asks the daemon
    // for a repaint pulse, cleared only where `term.reset()` discards what was parsed
    // (a reconnect or resync replay), so a short transcript cannot re-request forever.
    let scrollbackRepaintRequested = false
    // Write-pipeline liveness: byte arrival vs parse progress. A parser exception
    // kills xterm's write loop without any event this pane could subscribe to, so
    // the health sweep compares these two clocks instead (`writePipelineStalled`).
    let lastBytesAt: number | null = null
    let lastParsedAt: number | null = null
    // Unlike WebSocket delivery, xterm's write callback means the bytes reached the
    // parser. Acknowledging that boundary lets the daemon cap the queue ahead of echo.
    const pendingLiveWrites: { bytes: number; at: number }[] = []
    let pendingLiveWriteBytes = 0
    let outputAckBytes = 0
    let outputAckTimer: number | undefined
    let outputAckSocket: WebSocket | null = null
    // Keystrokes typed while the buffer replays. Returning to a tab always re-attaches
    // (and a long absence reconnects), so the tap-and-type right after coming back lands
    // mid-replay; sending it then would race the replayed bytes, and dropping it loses
    // characters the mobile IME baseline has already advanced past. Hold and flush in order.
    const pendingUserInput: { data: string; broadcast: boolean; capture: TerminalInputCapture | null }[] = []
    let pendingUserInputLength = 0
    const inputLatency = new TerminalInputLatencyTracker()
    const inputTextEncoder = new TextEncoder()
    let pendingInputCapture: { eventAt: number; source: TerminalInputSource } | null = null
    let lastHumanInputAt: number | null = null
    let currentGeneration = session._snapshot_generation || ''
    let currentRevision = session._snapshot_generation ? Number(session._snapshot_revision ?? -1) : -1
    let lastReplyTriggerState=session.state
    let exitWritten = false
    let fitFrame = 0
    let redrawFrame = 0
    let visibilityFrame = 0
    let surfaceConfirmTimer: number | undefined
    let surfaceRepair: SurfaceRepairScheduler | null = null
    let invalidateAtlasOnRedraw = false
    let webgl: WebglAddon | null = null
    let activeRenderer: ActiveTerminalRenderer = 'dom'
    let awaitingFullRedraw = false
    const diagnoseRender = terminalRenderDiagnosticsEnabled
      ? (phase: string, detail?: Record<string, unknown>) => recordTerminalRenderDiagnostic(session.id, phase, detail)
      : undefined
    // Repairs are the one diagnostic class that must reach the daemon even when the
    // opt-in in-page ring buffer is off: they happen in production browsers, and
    // whether each repair layer still fires is what decides its future. The daemon
    // allowlists the phases and rate-limits per session; the local throttle only
    // keeps a pathological repair loop from flooding the socket.
    let lastRepairReportAt = 0
    const reportRepair = (phase: string, detail?: Record<string, unknown>) => {
      diagnoseRender?.(phase, detail)
      if (socket?.readyState !== WebSocket.OPEN) return
      const now = performance.now()
      if (now - lastRepairReportAt < 1000) return
      lastRepairReportAt = now
      socket.send(JSON.stringify({ type: 'client_diagnostic', phase, detail }))
    }
    const lastInputDiagnosticReportAt = new Map<string, number>()
    const reportInputDiagnostic = (phase: string, detail?: Record<string, unknown>) => {
      diagnoseRender?.(phase, detail)
      if (socket?.readyState !== WebSocket.OPEN) return
      const now = performance.now()
      if (now - (lastInputDiagnosticReportAt.get(phase) ?? 0) < 1000) return
      lastInputDiagnosticReportAt.set(phase, now)
      socket.send(JSON.stringify({
        type: 'client_diagnostic',
        phase,
        detail: {
          ...detail,
          device,
          owner: ownsInput,
          paneHidden: paneIsHidden(),
          online: navigator.onLine,
          replaying,
          socketBufferedBytes: socket.bufferedAmount,
          pendingOutputBytes: pendingLiveWriteBytes,
          renderer: activeRenderer,
        },
      }))
    }
    const stopInputStallWatch = watchMainThreadStalls(stall => {
      if (lastHumanInputAt === null || paneIsHidden()) return
      const inputAgeAtStart = stall.startedAt - lastHumanInputAt
      if (inputAgeAtStart < 0 || inputAgeAtStart > 10_000) return
      reportInputDiagnostic('input_main_thread_stall', {
        durationMs: stall.durationMs,
        inputAgeAtStartMs: Math.round(inputAgeAtStart),
      })
    })
    const flushOutputAck = (source = outputAckSocket) => {
      if (!source || socket !== source || source.readyState !== WebSocket.OPEN) return
      // A retained warm pane is not interactive. Withhold credit after its first
      // bounded window so busy hidden agents do not keep consuming the UI thread.
      if (paneIsHidden()) return
      const bytes = outputAckBytes
      outputAckBytes = 0
      if (bytes > 0) source.send(JSON.stringify({ type: 'output_ack', bytes }))
    }
    const acknowledgeParsedOutput = (source: WebSocket, byteCount: number) => {
      if (socket !== source || source.readyState !== WebSocket.OPEN) return
      if (outputAckSocket !== source) {
        if (outputAckTimer !== undefined) window.clearTimeout(outputAckTimer)
        outputAckSocket = source
        outputAckBytes = 0
        outputAckTimer = undefined
      }
      outputAckBytes += byteCount
      if (outputAckTimer !== undefined) return
      // Collapse adjacent xterm callbacks into one small control frame. Sixteen
      // milliseconds still keeps the daemon credit window tight enough for echo.
      outputAckTimer = window.setTimeout(() => {
        outputAckTimer = undefined
        flushOutputAck(source)
      }, 16)
    }
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
    // Always registered, not diagnostics-gated: a WebGL renderer failure in a
    // production browser is precisely the event the durable repair log exists for.
    const onRenderError = (event: ErrorEvent) => {
      if (!isWebglRenderError(event)) return
      reportRepair('webgl_render_error', { message: event.message, renderer: activeRenderer })
    }
    window.addEventListener('error', onRenderError)
    const loadLatestReply=()=>{
      if(!isAgentBackend(session.backend))return
      void api<{text:string}>('GET',`/api/sessions/${session.id}/last-reply`).then(result=>{
        // Clear on an empty result too: keeping the previous value here is what
        // let a session with no reply yet still answer "Copy reply".
        if(!disposed)setLastReply(result.text||'')
      }).catch(()=>{if(!disposed)setLastReply('')})
    }
    const scheduleLatestReply=(delay=700)=>{
      if(!isAgentBackend(session.backend))return
      if(replyRefreshTimer!==undefined)window.clearTimeout(replyRefreshTimer)
      replyRefreshTimer=window.setTimeout(()=>{replyRefreshTimer=undefined;loadLatestReply()},delay)
    }
    const reportTerminalState = () => {
      if (socket?.readyState !== WebSocket.OPEN) return
      socket.send(JSON.stringify({ type: 'terminal_state', mode: term.buffer.active.type }))
    }
    const bufferChange = term.buffer.onBufferChange(() => {
      cancelCaretPlacement()
      reportTerminalState()
      // An alternate-screen switch is the application that owned the tracked viewport
      // starting or exiting, so the estimate describes a viewport that is no longer on
      // screen. `syncTail` answers for whatever replaced it.
      clearAppTail('buffer_switch')
    })
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
    // Parse-progress clock for the write-pipeline liveness check below.
    const writeParsed = term.onWriteParsed(() => {
      lastParsedAt = performance.now()
      scheduleKeyboardSettleRef.current()
    })
    // The invariant half of terminal display correctness (`terminalHealth.ts`):
    // every event path can be raced or missed, so on the same slow clock as the
    // terminal-state report, compare what the pane shows against what it should
    // show and route any drift through the ordinary repair machinery.
    const sweepTerminalHealth = () => {
      if (disposed) return
      const now = performance.now()
      const oldestLiveWriteAt = pendingLiveWrites[0]?.at ?? null
      if (writePipelineBacklogged(pendingLiveWriteBytes, oldestLiveWriteAt, now)) {
        reportRepair('write_pipeline_backlog', {
          pendingBytes: pendingLiveWriteBytes,
          pendingWrites: pendingLiveWrites.length,
          oldestAgeMs: oldestLiveWriteAt === null ? 0 : Math.round(now - oldestLiveWriteAt),
          renderer: activeRenderer,
        })
      }
      if (
        socket?.readyState === WebSocket.OPEN
        && writePipelineStalled(lastBytesAt, lastParsedAt, now)
      ) {
        reportRepair('write_pipeline_dead', {
          lastBytesAt,
          lastParsedAt,
          replaying,
          pendingReplayWrites,
          renderer: activeRenderer,
        })
        requestPaneRemountRef.current()
        return
      }
      if (surfaceDrifted(
        confirmedSurface,
        terminalSurface(term, host.current),
        replaying,
        paneIsHidden(),
      )) {
        reportRepair('surface_drift_repair', {
          confirmed: confirmedSurface,
          cols: term.cols,
          rows: term.rows,
        })
        scheduleFit()
        return
      }
      const proposedFit = paneIsHidden() || letterboxed ? undefined : fit.proposeDimensions()
      if (terminalFitDrifted(
        term,
        proposedFit,
        replaying,
        paneIsHidden(),
        letterboxed,
      )) {
        reportRepair('viewport_fit_drift_repair', {
          current: { cols: term.cols, rows: term.rows },
          proposed: proposedFit,
          host: { width: host.current?.clientWidth ?? 0, height: host.current?.clientHeight ?? 0 },
        })
        scheduleFit()
        return
      }
      if (viewportFitOwed && !paneIsHidden() && terminalHostIsVisible(host.current)) {
        reportRepair('viewport_fit_resumed', {
          cols: term.cols,
          rows: term.rows,
          host: { width: host.current?.clientWidth ?? 0, height: host.current?.clientHeight ?? 0 },
        })
        scheduleFit()
        return
      }
      if (surfaceRepair?.owed && !paneIsHidden() && terminalHostIsVisible(host.current)) {
        reportRepair('surface_repair_resumed', {
          cols: term.cols,
          rows: term.rows,
          renderer: activeRenderer,
        })
        surfaceRepair.resume()
      }
    }
    // Both keyboard-layout questions are answered from one read of the grid, and both only
    // mean anything once the harness has stopped painting: whether this pane may hold the
    // keyboard's height back out of its own geometry, and whether output has landed in the
    // half of the grid the keyboard is covering. Asking per write would run them hundreds of
    // times through a streaming reply and read a half-drawn screen every time.
    let keyboardSettleTimer: number | undefined
    // The hidden region as it read at the last settle, and the inset it was read under. The
    // inset is half of it because the region only exists while the keyboard is up: a change
    // measured across the keyboard opening or closing is the geometry moving, not output.
    let hiddenRegionText = ''
    let hiddenRegionInset = -1
    const gridRowText = (index: number) =>
      term.buffer.active.getLine(term.buffer.active.baseY + index)?.translateToString(true) ?? ''
    const evaluateKeyboardReserve = () => {
      const rowHeight = paneRowHeight()
      const reservePx = reservedKeyboardPx(lastSoftKeyboardInset(), window.innerHeight)
      reservePxRef.current = reservePx
      const decision = nextReserveState({
        reserved: keyboardReservedRef.current,
        rows: term.rows,
        reserveRows: rowHeight > 0 ? Math.ceil(reservePx / rowHeight) : term.rows,
        painted: paintedRowCount(term.rows, gridRowText),
        // A letterboxed pane draws another device's geometry and must not push its own at
        // the PTY; a hidden one has no keyboard over it. Both give the space back.
        eligible: compactLayoutRef.current
          && typesWithSoftKeyboard()
          && !letterboxed
          && !paneIsHidden()
          && reservePx > 0,
        // Up, or on its way. Without this the strip was held for the life of the pane —
        // the only release was the session's own content filling the smaller grid, which
        // an agent TUI with whitespace in its layout never reaches.
        keyboardWanted: keyboardInsetRef.current > 0
          || Date.now() - typingIntentAtRef.current < RESERVE_INTENT_WINDOW_MS,
        // A replaying buffer reads emptier than the session is, and reserving on that
        // reading would shrink a PTY whose content simply has not arrived yet.
        measurable: !replaying && socket?.readyState === WebSocket.OPEN,
        now: Date.now(),
        changedAt: reserveChangedAtRef.current,
      })
      if (decision.reserved === keyboardReservedRef.current) return
      keyboardReservedRef.current = decision.reserved
      reserveChangedAtRef.current = Date.now()
      setKeyboardReserved(decision.reserved)
      recordTerminalRenderDiagnostic(session.id, 'keyboard_reserve', {
        reserved: decision.reserved,
        reason: decision.reason,
        reservePx,
        rows: term.rows,
        painted: paintedRowCount(term.rows, gridRowText),
      })
    }
    const checkHiddenOutput = () => {
      const inset = effectiveKeyboardInsetRef.current
      const rowHeight = paneRowHeight()
      const hiddenRows = inset > 0 && rowHeight > 0 ? Math.min(term.rows, Math.floor(inset / rowHeight)) : 0
      let text = ''
      for (let index = 0; index < hiddenRows; index += 1) text += `${gridRowText(index)}\n`
      const comparable = hiddenRegionInset === inset
      const changed = comparable && text !== hiddenRegionText
      hiddenRegionText = text
      hiddenRegionInset = inset
      let visiblePainted = 0
      for (let index = hiddenRows; index < term.rows; index += 1) {
        if (gridRowText(index).trim() !== '') visiblePainted += 1
      }
      if (!hiddenOutputDeservesPeek({
        hiddenChanged: changed,
        hiddenHasText: text.trim() !== '',
        visiblePainted,
        peekOffset: peekOffsetRef.current,
        sinceInputMs: lastHumanInputAt === null ? null : performance.now() - lastHumanInputAt,
        inputGraceMs: HIDDEN_OUTPUT_INPUT_GRACE_MS,
      })) return
      applyPeekRef.current('hiddenOutput')
    }
    const scheduleKeyboardSettle = () => {
      if (keyboardSettleTimer !== undefined) window.clearTimeout(keyboardSettleTimer)
      keyboardSettleTimer = window.setTimeout(() => {
        keyboardSettleTimer = undefined
        if (disposed) return
        evaluateKeyboardReserve()
        checkHiddenOutput()
      }, KEYBOARD_LAYOUT_SETTLE_MS)
    }
    scheduleKeyboardSettleRef.current = scheduleKeyboardSettle
    paintPeekOffsetRef.current = (offset: number) => {
      const surface = host.current?.parentElement
      if (!surface) return
      // The animation belongs to the jumps (the toggle, landed output), and a transition on a
      // length the finger is already setting reads as the pane lagging the drag.
      surface.classList.remove('keyboard-peek-animated')
      surface.classList.toggle('keyboard-peek', offset > 0)
      surface.style.setProperty('--peek-offset', `${offset}px`)
    }
    const terminalStateTimer = window.setInterval(() => {
      reportTerminalState()
      sweepTerminalHealth()
      // Backstop for a session that has stopped writing entirely: the settle timer is driven
      // by output, and a pane that grew into its reserved grid and then went quiet would
      // otherwise keep holding space back.
      evaluateKeyboardReserve()
    }, TERMINAL_HEALTH_SWEEP_MS)
    // Shared-PTY geometry. `localFit` is what this pane would show at its own font size;
    // `serverGeometry` is what the daemon arbitrated across every attached device. They
    // differ exactly when another device owns input, and then this pane letterboxes
    // rather than pushing its own dimensions back at the PTY.
    let localFit: { cols: number; rows: number } | null = null
    let localFitBox: { width: number; height: number } | null = null
    let serverGeometry: { cols: number; rows: number } | null = null
    let sentViewport: { cols: number; rows: number; hidden: boolean } | null = null
    // Whether the current viewport pass shipped a `resize` frame — the signal that its
    // real cost includes a pseudoconsole resize and a CLI repaint, not just local work.
    let viewportResizeSent = false
    let letterboxed = false
    // Armed by the visibility transition, consumed by the next `applyGeometry`. A single
    // pass, not a mode: the licence covers the one measurement taken as the pane comes
    // back, and everything after it is an ordinary resize that must letterbox normally.
    let adoptOwnFitOnReveal = false
    // Consecutive frames this pass has waited for a revealed host to gain layout.
    let measureRetries = 0
    // A visible viewport request is not complete until FitAddon returns real dimensions.
    // Bounded frame retries may pause, but they cannot erase this debt.
    let viewportFitOwed = false
    const sendViewport = (cols: number, rows: number, force = false) => {
      if (socket?.readyState !== WebSocket.OPEN) return
      const hidden = paneIsHidden()
      if (!force && sentViewport?.cols === cols && sentViewport.rows === rows && sentViewport.hidden === hidden) return
      sentViewport = { cols, rows, hidden }
      viewportResizeSent = true
      // `hidden` deregisters this viewport instead of registering it: a minimized
      // window still reports layout, and it must not reshape the PTY for the device
      // the user is actually holding.
      socket.send(JSON.stringify({ type: 'resize', cols, rows, hidden }))
    }
    const measureFit = (): boolean => {
      const box = host.current
      if (!box) return false
      const size = { width: box.clientWidth, height: box.clientHeight }
      // While letterboxed, measuring means briefly restoring the pane's own font, so it
      // is done only when the box it has to fit actually changed.
      if (letterboxed && localFit && localFitBox?.width === size.width && localFitBox.height === size.height) {
        sendViewport(localFit.cols, localFit.rows)
        return true
      }
      // A letterboxed pane renders at a smaller font, and a proposal measured there
      // would tell the daemon this pane wants columns nobody on this device could read
      // — and that is the number the shared PTY would then be sized to.
      const proposed = proposeNormalDimensions(true)
      if (!proposed || !Number.isFinite(proposed.cols) || !Number.isFinite(proposed.rows)) return false
      localFit = { cols: proposed.cols, rows: proposed.rows }
      localFitBox = size
      sendViewport(localFit.cols, localFit.rows)
      return true
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
        baseFontSize: baseFontRef.current,
      })
      const fontSize = term.options.fontSize ?? baseFontRef.current
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
      term.options.fontSize = normalFontSize
    }
    const applyGeometry = () => {
      // Consume the reveal's licence to trust its own measurement before comparing, so
      // a pane the daemon has not answered yet draws the grid it is actually going to
      // keep instead of one frame of the size it had before it was hidden.
      if (adoptOwnFitOnReveal) {
        adoptOwnFitOnReveal = false
        if (adoptsOwnGeometryOnReveal(ownsInput, localFit)) serverGeometry = localFit
      }
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
        // No renderer reflow here, deliberately. Restoring the font is enough: xterm
        // re-measures its surface on a font change even when the grid is unchanged, so
        // the stale-dimension repair `reflowVisibleTerminalRenderer` exists for does not
        // apply to this path. Measured in `runLetterboxExitRepair`, which asserts it.
        term.options.fontSize = baseFontRef.current
      }
      fitVisiblePane()
    }
    // The width of the *track* the host sits in, which is the pane the user drags.
    // Deliberately not the host's own width: past the cap the host stops moving
    // altogether, so a pane that is already capped and dragged wider produces no host
    // resize at all - exactly the case that needs explaining. Zero until the first
    // measurement, so a restored wide layout explains itself on the first drag rather
    // than at boot.
    let lastTrackWidth = 0
    const judgeWidthCap = () => {
      const box = host.current
      const track = box?.parentElement
      if (!box || !track) return
      const trackWidth = track.clientWidth
      const moved = lastTrackWidth > 0 && trackWidth !== lastTrackWidth
      lastTrackWidth = trackWidth
      // Recorded before the bail so a pane that resized while hidden does not read as
      // a fresh drag on the frame it comes back.
      if (paneIsHidden() || !trackWidth) return
      const cap = widthCapRef.current
      if (!claudeWidthCapClamping(box, cap)) { hideWidthCapNotice.current(); return }
      if (moved) showWidthCapNotice.current(cap)
    }
    const runViewportPass = () => {
      viewportFitOwed = true
      window.cancelAnimationFrame(fitFrame)
      window.cancelAnimationFrame(redrawFrame)
      fitFrame = window.requestAnimationFrame(() => {
        // Deregister before doing any geometry work. Warm panes deliberately retain a
        // measurable box so xterm never passes through a zero-sized renderer, but that
        // box must not refit the local model while its PTY remains at the last visible
        // geometry or register a viewport for a tab nobody is looking at.
        if (paneIsHidden()) {
          sendViewport(localFit?.cols ?? term.cols, localFit?.rows ?? term.rows)
          viewportFitOwed = false
          measureRetries = 0
          return
        }
        const viewportBox = host.current
        const viewportBoxState = {
          width: viewportBox?.clientWidth ?? 0,
          height: viewportBox?.clientHeight ?? 0,
        }
        if (!terminalHostIsVisible(viewportBox)) {
          // A pane revealed this frame can still measure zero while layout settles, and
          // this pass is the only thing that would register its real viewport. Retry a
          // bounded number of frames instead of dropping it: see
          // VIEWPORT_MEASURE_RETRY_FRAMES for why the ResizeObserver is not the net it
          // looks like. A pane that is genuinely hidden stopped at the check above.
          if (!paneIsHidden()) diagnoseRender?.('viewport_fit_deferred', {
            reason: 'host_unmeasurable',
            retries: measureRetries,
            host: viewportBoxState,
          })
          if (!paneIsHidden() && measureRetries < VIEWPORT_MEASURE_RETRY_FRAMES) {
            measureRetries += 1
            runViewportPass()
          }
          return
        }
        // A resize moves `baseY` (a ConPTY-backed buffer gains blank rows rather than
        // pulling scrollback back down), so a viewport that was on the newest line no
        // longer is. Remembered before the fit and restored after, because "was the
        // user reading the tail" is not answerable once the grid has changed — and
        // yanking someone who had deliberately scrolled up is worse than not.
        const wasAtTail = !offTailRef.current
        // Where a reader who had scrolled up was, so the same text can be put back under
        // them. Only the at-tail case used to be preserved, which left every other
        // position to whatever the resize and the scroller's stale range did with it.
        const rowsAboveTail = wasAtTail ? 0 : terminalRowsAboveTail(term)
        const startedAt = performance.now()
        viewportResizeSent = false
        if (!measureFit()) {
          diagnoseRender?.('viewport_fit_deferred', {
            reason: 'dimensions_unavailable',
            retries: measureRetries,
            host: { width: host.current?.clientWidth ?? 0, height: host.current?.clientHeight ?? 0 },
          })
          if (!paneIsHidden() && measureRetries < VIEWPORT_MEASURE_RETRY_FRAMES) {
            measureRetries += 1
            runViewportPass()
          }
          return
        }
        measureRetries = 0
        applyGeometry()
        viewportFitOwed = false
        // Timed around the two calls that do the work, so the scheduler's decision to
        // coalesce is based on this pane's real cost rather than on its backend — and a
        // pass that shipped a `resize` frame is charged the pseudoconsole resize and CLI
        // repaint it caused downstream, which the local clock cannot see
        // (`effectiveViewportCost`).
        viewportScheduler.observeCost(
          effectiveViewportCost(performance.now() - startedAt, viewportResizeSent),
        )
        // A successful measurement is the first reliable point after reveal at which
        // renderer repair can run. Resume any debt that a zero-sized frame retained.
        surfaceRepair?.resume()
        if (wasAtTail) scrollTerminalToTail(term)
        else restoreTerminalScrollAnchor(term, rowsAboveTail)
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
          armSurfaceConfirmation(fullRedraw || terminalSurfaceChanged(confirmedSurface, terminalSurface(term, host.current)))
        })
      })
    }
    // The chrome scale moved, so this pane's font did. Re-measure through the ordinary
    // pass: this device proposes whatever grid its new type fits and arbitration settles
    // it, exactly as a window resize does. Deliberately *not* `resizeToPaneRef`, which
    // claims the geometry — changing your own font is not a decision to take the shared
    // PTY away from another device.
    applyBaseFontRef.current = () => {
      if (term.options.fontSize === baseFontRef.current) return
      // A letterboxed pane sits on a fitted font derived from the base. `applyGeometry`
      // recomputes that from the new base, so assigning here would only flash the wrong
      // size for a frame.
      if (!letterboxed) term.options.fontSize = baseFontRef.current
      // The cached fit was measured at the old font and would otherwise be believed.
      localFit = null
      localFitBox = null
      runViewportPass()
    }
    // Repaint the surface again, without touching geometry.
    //
    // Deliberately not another viewport pass: a fit is `term.resize` plus a pseudoconsole
    // resize plus the CLI repainting everything it is showing (see EXPENSIVE_VIEWPORT_PASS_MS),
    // and none of that is what needs repeating. Only the two calls that put pixels back do.
    // The surface this pane last confirmed it had drawn, so a pass can tell whether the one
    // it just painted is a different shape. Written only by the confirmation, never by the
    // pass that requests it: a pass that painted into a moving layout is precisely the one
    // whose result must not be believed.
    let confirmedSurface: TerminalSurface | null = null
    surfaceRepair = createSurfaceRepairScheduler(
      () => {
        const box = host.current
        const boxState = {
          connected: box?.isConnected ?? false,
          width: box?.clientWidth ?? 0,
          height: box?.clientHeight ?? 0,
        }
        if (paneIsHidden() || !terminalHostIsVisible(box)) {
          diagnoseRender?.('surface_redraw_deferred', {
            ...boxState,
            paneHidden: paneIsHidden(),
            renderer: activeRenderer,
          })
          return false
        }
        webgl?.clearTextureAtlas()
        // Renderer dimensions first, then pixels. A pass whose box moved without its grid
        // changing leaves the renderer sized for the old box, and refreshing rows into a
        // surface that is still the wrong size repaints exactly the region that was
        // already right. FitAddon's same-grid early return is what makes this reachable —
        // see `reflowVisibleTerminalRenderer`. Free when nothing is stale.
        const reflowed = reflowVisibleTerminalRenderer(term, box)
        const redrawn = redrawVisibleTerminal(term, box)
        confirmedSurface = terminalSurface(term, box)
        if (!reflowed || !redrawn || !confirmedSurface) {
          diagnoseRender?.('surface_redraw_deferred', {
            reflowed,
            redrawn,
            hasSurface: !!confirmedSurface,
            renderer: activeRenderer,
          })
          confirmedSurface = null
          return false
        }
        diagnoseRender?.('surface_redraw_confirmed', {
          cols: term.cols,
          rows: term.rows,
          renderer: activeRenderer,
        })
        if (viewportFitOwed) runViewportPass()
        return true
      },
      () => !paneIsHidden(),
      {
        requestFrame: fn => window.requestAnimationFrame(fn),
        cancelFrame: id => window.cancelAnimationFrame(id),
      },
    )
    // A redraw races the compositor: the frame it lands on may be one where this pane's
    // canvas is not being presented at its final size yet, and the render is then simply lost
    // — xterm's RenderService fires `onRender` whether or not the renderer drew anything, so
    // nothing anywhere retries it. One confirmation after the burst has settled costs an atlas
    // clear and covers that, and unlike the redraw itself it cannot be raced by layout.
    //
    // Armed for any pass that reshaped the surface, not only for the atlas-invalidating ones.
    // Gating it on the atlas left the single resize path most exposed to that race as the one
    // path with no confirmation at all: a soft keyboard animates for ~250-400 ms and refits
    // this pane throughout, so the settling pass reliably paints into a layout still in
    // motion. What the reader sees is the strip the keyboard just vacated staying blank, with
    // nothing to retry it — the pane is only asked to redraw again when something else happens.
    const armSurfaceConfirmation = (issued: boolean) => {
      if (!issued) return
      surfaceRepair?.markOwed()
      window.clearTimeout(surfaceConfirmTimer)
      surfaceConfirmTimer = window.setTimeout(() => surfaceRepair?.request(), VIEWPORT_SETTLE_MS)
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
      window.cancelAnimationFrame(visibilityFrame)
      if (!nowVisible) {
        cancelCaretPlacement()
        // Send the deregistration directly. The warm host remains measurable on
        // purpose, but no scheduled geometry work may run for a hidden viewport.
        // Unconditional in the dimensions: `sendViewport` reads `hidden` itself, so what
        // matters is that the frame goes at all. Gating it on a `localFit` this pane may
        // never have taken (a reconnect nulls it) is what let a registration outlive the
        // pane's own visibility and hold the PTY at a size nobody was looking at.
        sendViewport(localFit?.cols ?? term.cols, localFit?.rows ?? term.rows)
        // Stop rendering while retained: the model keeps parsing, renders defer, and
        // the reveal below resumes with xterm's own remeasure-and-repaint recovery.
        renderControl.pause()
        return
      }
      // Back on screen. The pane may have missed geometry changes while hidden, and
      // its renderer may hold pixels from before the tab was resized, so this is a
      // full redraw rather than a plain fit. FitAddon skips its
      // resize when the grid is unchanged, but xterm's renderer can still hold stale
      // pixel dimensions after a retained hidden interval; force the same-grid renderer reflow
      // after the scheduled fit. The tail scroll is the same repair `finishReplay`
      // does: output that arrived while the pane was hidden moved `baseY` without
      // moving a viewport nobody was watching.
      //
      // The measurement that pass is about to take is also the one that must not be
      // second-guessed against a `serverGeometry` predating the hide: see
      // `adoptsOwnGeometryOnReveal`. Armed here rather than inside the pass because
      // only the reveal knows the next measurement is the reveal's.
      adoptOwnFitOnReveal = true
      // A pane that letterboxed on the way out is rendering at a shrunk font, and the
      // cached fit was measured in that state. Dropping both makes the reveal's pass a
      // real measurement of the box the user is now looking at instead of a cache hit
      // on `localFitBox`, which is what let a stale grid survive the whole transition.
      localFitBox = null
      // Resume rendering first: everything scheduled below paints through the
      // RenderService, and a still-paused service would defer it all into a flag
      // nobody flushes until the next resize.
      renderControl.resume()
      flushOutputAck()
      // Record this before scheduling either frame. The debt survives if both land while
      // the host still measures zero and is resumed by the first successful measurement.
      surfaceRepair?.markOwed()
      scheduleFullRedraw()
      // A warm pane attaches hidden, so its replay finished with the repaint request
      // suppressed. First reveal is when the missing transcript would become visible.
      maybeRequestScrollbackRepaint()
      visibilityFrame = window.requestAnimationFrame(() => {
        if (paneIsHidden()) return
        surfaceRepair?.resume()
        // Only for a viewport that was on the tail when the pane went away. A reader who
        // had deliberately scrolled up is not asking to be taken to the newest output every
        // time they visit another tab, and the pass above has already put their anchor back.
        if (!offTailRef.current) scrollTerminalToTail(term)
      })
    }
    // Chromium device emulation can preserve a live WebGL context while changing
    // its emulated pixel ratio, leaving xterm interactive but visually blank.
    // The built-in renderer is reliable for the single full-screen mobile pane.
    const mobileRenderer = window.matchMedia('(max-width:760px)').matches
    // Claude is DOM-only because its alternate-screen WebGL surface can remain live
    // but corrupt after a retained hidden interval; there is no context-loss event to
    // recover from.
    // Under `auto`, scrollback-repainting harnesses also remain on DOM.
    if (shouldLoadWebgl(rendererPreference, mobileRenderer, session.backend)) {
      try {
        // `preserveDrawingBuffer: true`, and it is not optional here.
        //
        // WebglRenderer._updateModel skips any cell whose code, fg, bg and ext all match
        // its model ("Nothing has changed, no updates needed"), so a frame re-uploads only
        // what changed and every other pixel is expected to still be in the drawing buffer.
        // With the default `false` the spec lets the browser discard that buffer once the
        // canvas stops being composited, which is exactly what a warm pane behind another
        // tab can be. Coming back, the model still claims
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
    const preconnectFit = fitVisiblePane()
    // A pane mounted warm (a reload's warm-mount) never receives a visibility
    // transition, so the render pause has to start here. The pane axis, not
    // `paneIsHidden()`: a hidden *document* still resumes through rAF throttling,
    // and only the reveal path below knows how to undo this pause.
    if (!visibleRef.current) renderControl.pause()
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
      if (!fitVisiblePane()) return
      reflowVisibleTerminalRenderer(term, box)
      localFit = { cols: term.cols, rows: term.rows }
      localFitBox = { width: box.clientWidth, height: box.clientHeight }
      serverGeometry = localFit
      sendViewport(localFit.cols, localFit.rows, true)
      lastInteractionAt = Date.now()
      claimInput('gesture')
      surfaceRepair?.request()
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
    const sendInput = (
      data: string,
      protocolResponse: boolean,
      broadcast: boolean,
      retry = false,
      capture: TerminalInputCapture | null = null,
    ) => {
      if (socket?.readyState !== WebSocket.OPEN) return
      // Protocol responses still go through: they answer a query that was in the
      // replayed bytes, and the daemon drops them for a dead session anyway.
      // Human input does not, and must not claim ownership on the way.
      if (readOnlyRef.current && !protocolResponse) return
      // Typing is itself evidence this pane should own input. Without it a pane
      // displaced by another device stays silently muted until the user happens
      // to click inside it. A retry has already claimed, so it must not claim twice.
      if (!protocolResponse) lastInteractionAt = Date.now()
      if (!protocolResponse && !retry && data === '\r' && isAgentBackend(backendRef.current)) {
        reportPromptSubmitted(session.id)
      }
      if (!ownsInput && !protocolResponse && !retry) claimInput('gesture')
      const diagnostic = !protocolResponse && capture
        ? inputLatency.begin(
          capture,
          inputTextEncoder.encode(data).byteLength,
          performance.now(),
          Date.now(),
          socket.bufferedAmount,
        )
        : null
      socket.send(JSON.stringify({
        type: 'input',
        data,
        kind: protocolResponse ? 'terminal_response' : 'user',
        broadcast: protocolResponse ? false : broadcast,
        retry,
        ...diagnostic?.frame,
      }))
      if (diagnostic && diagnostic.frame.client_event_delay_ms >= INPUT_LATENCY_REPORT_MS) {
        reportInputDiagnostic('input_event_delay', {
          inputSeq: diagnostic.probe.seq,
          source: diagnostic.probe.source,
          eventToSendMs: diagnostic.frame.client_event_delay_ms,
          queueMs: diagnostic.frame.client_queue_delay_ms,
          bufferedBefore: diagnostic.frame.ws_buffered_bytes,
        })
      }
      if (diagnostic && diagnostic.frame.ws_buffered_bytes >= 64 * 1024) {
        reportInputDiagnostic('input_socket_backlog', {
          inputSeq: diagnostic.probe.seq,
          source: diagnostic.probe.source,
          bufferedBefore: diagnostic.frame.ws_buffered_bytes,
        })
      }
    }
    const wheelPacer = createWheelPacer(
      (data, broadcast) => sendInput(data, false, broadcast),
      {
        now: () => performance.now(),
        schedule: fn => window.requestAnimationFrame(fn),
        cancel: id => window.cancelAnimationFrame(id),
      },
    )
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
    // A wrapped ring replays only live-region repaint traffic, and only this client can
    // see that its parse produced no scrollback (`scrollbackRepaintNeeded`). Asking the
    // daemon for a pulse makes the child restate its transcript, which both fills this
    // pane and repopulates the ring for every later attach. Checked when a replay
    // finishes on a visible pane and again when a hidden-attached warm pane is first
    // revealed — the path the attach-time pulse never covered.
    const maybeRequestScrollbackRepaint = () => {
      if (scrollbackRepaintRequested || replaying || paneIsHidden()) return
      if (socket?.readyState !== WebSocket.OPEN) return
      const buffer = term.buffer.active
      if (!scrollbackRepaintNeeded(repaintsScrollback(session.backend), buffer.type, buffer.baseY, term.rows)) return
      scrollbackRepaintRequested = true
      socket.send(JSON.stringify({ type: 'repaint' }))
      reportRepair('scrollback_repaint_requested', { baseY: buffer.baseY, rows: term.rows })
    }
    const finishReplay = () => {
      replaying = false
      replayAllowsTerminalResponses = false
      attachmentReadyRef.current = true
      setAttachmentReady(true)
      const queued = pendingUserInput.splice(0, pendingUserInput.length)
      pendingUserInputLength = 0
      for (const item of queued) sendInput(item.data, false, item.broadcast, false, item.capture)
      scheduleFullRedraw()
      maybeRequestScrollbackRepaint()
      // A branch cut before one of the operator's own messages hands that message
      // back. This is the first moment the pane can take it: the composer exists and
      // the replay is no longer racing what is typed into it. Routed through the
      // pane's own action listener rather than calling `injectText` directly, so it
      // takes the same broadcast, read-mode and error path as any other insertion.
      // Inserted, never submitted - re-sending the prompt unedited would repeat the
      // request the branch existed to change.
      const seed = takeBranchSeed(session.id)
      if (seed) window.dispatchEvent(new CustomEvent('mux:terminal-action', {
        detail: { sessionId: session.id, action: 'insertText', text: seed },
      }))
    }
    const handleMessage=(event:MessageEvent, source:WebSocket)=>{
      if (event.data instanceof ArrayBuffer) {
        scheduleLatestReply()
        lastBytesAt = performance.now()
        const byteCount = event.data.byteLength
        if (replaying) {
          pendingReplayWrites += 1
          term.write(new Uint8Array(event.data), () => {
            pendingReplayWrites -= 1
            acknowledgeParsedOutput(source, byteCount)
            if (replayEndReceived && pendingReplayWrites === 0) finishReplay()
          })
        } else {
          // Live bytes advance the ring cursor; replay bytes never do (their end
          // position arrives on `replay_end`, and a full replay is not ring bytes).
          if (serverPosition !== null) serverPosition += byteCount
          // Output arriving is the ack the wheel pacer's clock runs on: the repaint
          // answering the last scroll report means the CLI is ready for the next one.
          wheelPacer.noteOutput()
          const outputAt = performance.now()
          const echoBatch = inputLatency.takeEchoBatch(outputAt)
          const pending = { bytes: byteCount, at: outputAt }
          pendingLiveWrites.push(pending)
          pendingLiveWriteBytes += byteCount
          term.write(new Uint8Array(event.data), () => {
            const parsedAt = performance.now()
            const index = pendingLiveWrites.indexOf(pending)
            if (index >= 0) pendingLiveWrites.splice(index, 1)
            pendingLiveWriteBytes = Math.max(0, pendingLiveWriteBytes - byteCount)
            acknowledgeParsedOutput(source, byteCount)
            if (echoBatch) {
              window.requestAnimationFrame(() => {
                if (disposed) return
                const diagnostic = inputLatency.completeEchoBatch(
                  echoBatch,
                  parsedAt,
                  performance.now(),
                )
                if (diagnostic) reportInputDiagnostic('input_echo_latency', diagnostic)
              })
            }
          })
        }
      }
      else {
        const frame = JSON.parse(event.data)
        // Dropped chunks broke the byte count; the resync's `replay_end` re-anchors it.
        if (frame.type === 'gap') { replaying = true; serverPosition = null }
        const frameGeneration=String(frame.snapshot?._snapshot_generation||'')
        if(frameGeneration&&frameGeneration!==currentGeneration){currentGeneration=frameGeneration;currentRevision=-1}
        if ((frame.type === 'state' || frame.type === 'update') && Number(frame.revision ?? 0) > currentRevision) {
          currentRevision = Number(frame.revision ?? 0)
          const nextState=frame.snapshot?.state as Session['state']|undefined
          if(nextState&&['idle','awaiting'].includes(nextState)&&nextState!==lastReplyTriggerState)scheduleLatestReply(250)
          if(nextState)lastReplyTriggerState=nextState
          onState(frame.snapshot)
        }
        if (frame.type === 'replay_start') {
          attachmentReadyRef.current=false
          setAttachmentReady(false)
          cancelCaretPlacement()
          // A delta continues this terminal's own byte stream, so the buffer —
          // scrollback, modes, scroll position — is kept and the missed bytes are
          // appended. Reset only what a delta cannot describe: a resync (this client
          // provably missed bytes the replay does not patch over), or a reconnect the
          // daemon answered with a full window (`attach`, including every daemon that
          // predates deltas).
          if (frame.reason === 'resync' || (reconnectReplay && frame.reason !== 'delta')) {
            term.reset()
            serverPosition = null
            // The buffer this flag described no longer exists; the fresh replay
            // earns its own missing-scrollback judgement.
            scrollbackRepaintRequested = false
          }
          reconnectReplay=false
          replaying = true
          replayEndReceived = false
          replayAllowsTerminalResponses = frame.reason === 'attach' && frame.allow_terminal_responses === true
        }
        if (frame.type === 'replay_end') {
          // The daemon's anchor for this pane's byte cursor. Its absence (a daemon
          // predating deltas) leaves the cursor null, which simply never offers
          // `since` — the old full-replay behaviour.
          const anchor = Number(frame.position)
          serverPosition = Number.isSafeInteger(anchor) && anchor >= 0 ? anchor : null
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
          if(!ownsInput)cancelCaretPlacement()
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
        if (frame.type === 'input_ack') {
          const inputSeq = Number(frame.input_seq)
          const serverReceivedAtMs = Number(frame.server_received_at_ms)
          const diagnostic = inputLatency.acknowledge(
            inputSeq,
            performance.now(),
            Number.isFinite(serverReceivedAtMs) ? serverReceivedAtMs : null,
          )
          if (diagnostic) reportInputDiagnostic('input_ack_latency', diagnostic)
        }
        if (frame.type === 'input_rejected') {
          inputLatency.reject(Number(frame.input_seq))
          replayRejectedInput(frame)
        }
        if (frame.type === 'geometry') {
          cancelCaretPlacement()
          const cols = Number(frame.cols)
          const rows = Number(frame.rows)
          if (Number.isFinite(cols) && Number.isFinite(rows)) {
            serverGeometry = { cols, rows }
            // A burst trigger, not a discrete one: geometry frames stream during any
            // continuous resize — the daemon answers every registration with one, so an
            // eager fit here re-measured a moved divider, sent the new grid, and that
            // registration's own geometry echo scheduled the next pass. That loop ran a
            // pseudoconsole resize (and a full CLI repaint) every websocket round-trip,
            // ~25/s per pane, for the whole gesture — the ResizeObserver's coalescing
            // never saw any of it. A lone frame (another device resized once) still
            // fits immediately while the last pass was cheap; only floods defer.
            scheduleBurstFit()
          }
        }
        if (frame.type === 'exit') {
          attachmentReadyRef.current=false
          setAttachmentReady(false)
          setConnectionState('ended')
          const exitGeneration=String(frame.snapshot?._snapshot_generation||'')
          if(exitGeneration&&exitGeneration!==currentGeneration){currentGeneration=exitGeneration;currentRevision=-1}
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
      if(disposed||!terminalAttachAllowed(['exited','crashed'].includes(stateRef.current),reconnecting))return
      attachmentReadyRef.current=false
      setAttachmentReady(false)
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
        const fitted = fitVisiblePane()
        // `document.hidden` is not this pane's visibility: a warm pane is logically hidden
        // inside a *foreground* tab, so it answered "visible", registered the 80x24 it
        // had never fitted, and — being unowned — took ownership and resized the session
        // to it. `paneIsHidden` is the pane's own axis, and the fit check covers the rest.
        const registers = attachRegistersViewport(fitted, paneIsHidden())
        // Seeded here so a pane that later goes hidden can withdraw the size it actually
        // registered, rather than waiting on a fit pass it will never be visible to run.
        if (registers) localFit = { cols: term.cols, rows: term.rows }
        next.send(JSON.stringify(terminalAttachReadyFrame(term.cols, term.rows, activeRenderer, !registers, serverPosition)))
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
        //
        // And a socket that opens a few hundred milliseconds after the user started
        // typing into a text field elsewhere - the sidebar filter, a rename dialog -
        // must not pull the keyboard out of it mid-word. Same principle as the passive
        // claim above: attaching is not the user asking for the keyboard.
        if(!reconnecting&&!mobileLiveInput&&!focusHeldByOtherField(activeEditableField()))term.focus()
      }
      next.onmessage=event=>{if(socket===next)handleMessage(event,next)}
      next.onclose=()=>{if(socket!==next)return;socket=null;attachmentReadyRef.current=false;setAttachmentReady(false);scheduleReconnect()}
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
      const onDataAt = performance.now()
      const capture = pendingInputCapture
        ? { ...pendingInputCapture, onDataAt }
        : null
      pendingInputCapture = null
      if(caretPlacementInputDepth===0)cancelCaretPlacement()
      // Typing is the reader saying they have stopped reading, so a pane peeking at the top
      // of its grid returns to the composer. Deliberately on input rather than on writes:
      // snapping back on output would fight the reader for the whole of a streaming reply.
      applyPeekRef.current('input')
      // The same sentence, one viewport further in. A submission moves the *application's*
      // own viewport to its newest output, and that is the one movement the pane forwards
      // nothing for and can never total, so the running estimate has to be dropped rather
      // than spent down. Placed with the peek reset because it is the same rule about the
      // same gesture, and ahead of the replay branch because queued bytes reach the CLI too.
      if(inputResetsAppTail(backendRef.current,data))clearAppTail('submit')
      const shouldBroadcast=attachmentSafeBroadcast(
        broadcastRef.current,
        attachmentPasteDepth+caretPlacementInputDepth+syntheticMouseInputDepth,
      )
      if (replaying) {
        if (pendingInputDecision(pendingUserInputLength, data.length) === 'overflow') {
          // Losing input without saying so is what made this look like the terminal
          // "eating" pastes; past the ceiling the pane owes the user an explanation.
          reportError('Input dropped: the terminal was still restoring its buffer.')
          return
        }
        pendingUserInputLength += data.length
        pendingUserInput.push({ data, broadcast: shouldBroadcast, capture })
        return
      }
      // Wheel scrolls to an application that holds the mouse go through the pacer:
      // xterm emits ~7 scroll reports per notch and the CLI drains them at its own
      // repaint rate, so a fast flick otherwise banks seconds of runaway scrolling
      // (see terminalWheelPacing). Everything else flushes the queue first — a click
      // or keystroke must not overtake the scrolls that preceded it.
      if (isWheelReportBurst(data)) {
        wheelPacer.push(data, shouldBroadcast)
        return
      }
      wheelPacer.flush()
      sendInput(data, false, shouldBroadcast, false, capture)
    })
    // View keys skip xterm's `input()` so they are never broadcast, and are dropped rather
    // than queued while replaying: the buffer is still being written, and a viewport gesture
    // that arrives seconds after the tap would move the user somewhere they no longer asked
    // for. It still claims input, since asking a session to scroll is this device using it.
    sendViewKeyRef.current = (sequence: string) => {
      if (replaying) return
      // A view command supersedes the wheel gesture: queued scroll reports landing
      // after jump-to-latest would drag the viewport straight back off the tail.
      wheelPacer.discard()
      sendInput(sequence, false, false)
    }
    let mobileInputValue=''
    const notePhysicalInput = (event: Event, source: TerminalInputSource) => {
      const now = performance.now()
      lastHumanInputAt = now
      pendingInputCapture = {
        eventAt: inputEventPerformanceTime(event.timeStamp, now, Date.now()),
        source,
      }
    }
    const terminalKeyCapture = (event: KeyboardEvent) => notePhysicalInput(event, 'keydown')
    const terminalBeforeInputCapture = (event: InputEvent) => notePhysicalInput(event, 'beforeinput')
    // A shell can promote into an agent harness without replacing the pane, so read
    // the live backend at the event rather than capturing the effect's first one.
    const mobileLineBreak=()=>mobileEnterPayload(backendRef.current)
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
    type CaretPlacement = {
      targetColumn: number
      targetRowOffset: number
      lastDirection: -1 | 1 | null
      precision: boolean
      keysSent: number
      startedAt: number
      pendingFrom: TerminalCaretPosition | null
      settleTimer: number | undefined
      watchdogTimer: number | undefined
    }
    let caretPlacement:CaretPlacement|null=null
    const sameCaretPosition=(a:TerminalCaretPosition,b:TerminalCaretPosition)=>a.column===b.column&&a.row===b.row
    const clearCaretTimers=(placement:CaretPlacement)=>{
      if(placement.settleTimer!==undefined)window.clearTimeout(placement.settleTimer)
      if(placement.watchdogTimer!==undefined)window.clearTimeout(placement.watchdogTimer)
      placement.settleTimer=undefined
      placement.watchdogTimer=undefined
    }
    const finishCaretPlacement=(outcome:string)=>{
      const placement=caretPlacement
      if(!placement)return
      clearCaretTimers(placement)
      caretPlacement=null
      diagnoseRender?.('caret_placement_finished',{
        outcome,
        keysSent:placement.keysSent,
        elapsedMs:Math.round(performance.now()-placement.startedAt),
      })
    }
    cancelCaretPlacement=()=>finishCaretPlacement('cancelled')
    const currentCaretPosition=():TerminalCaretPosition=>({
      column:term.buffer.active.cursorX,
      row:term.buffer.active.baseY+term.buffer.active.cursorY,
    })
    const runCaretPlacement=()=>{
      const placement=caretPlacement
      if(!placement||placement.pendingFrom)return
      const resolveCaret=caretResolverForBackend(backendRef.current)
      if(replaying||paneIsHidden()||!resolveCaret){
        finishCaretPlacement('unavailable')
        return
      }
      if(performance.now()-placement.startedAt>6500||placement.keysSent>=2048){
        finishCaretPlacement('limit')
        return
      }
      const snapshot=terminalCaretSnapshot(term)
      const resolved=resolveAnchoredCaretTarget(resolveCaret,snapshot,{
        column:placement.targetColumn,
        rowOffset:placement.targetRowOffset,
      })
      if(!resolved){
        finishCaretPlacement('composer_changed')
        return
      }
      if(sameCaretPosition(resolved.current,resolved.target)){
        finishCaretPlacement('placed')
        return
      }
      const command=caretSteerCommand(resolved.current,resolved.target,term.cols,placement.lastDirection)
      if(!command){finishCaretPlacement('placed');return}
      if(placement.lastDirection!==null&&command.direction!==placement.lastDirection)placement.precision=true
      const count=placement.precision?1:Math.min(command.count,2048-placement.keysSent)
      placement.lastDirection=command.direction
      placement.keysSent+=count
      placement.pendingFrom=resolved.current
      caretPlacementInputDepth+=1
      try{term.input(command.sequence.repeat(count),true)}finally{caretPlacementInputDepth-=1}
      placement.watchdogTimer=window.setTimeout(()=>{
        if(caretPlacement===placement&&placement.pendingFrom)finishCaretPlacement('no_progress')
      },1000)
    }
    const checkCaretPlacement=()=>{
      const placement=caretPlacement
      if(!placement||!placement.pendingFrom)return
      const current=currentCaretPosition()
      if(sameCaretPosition(current,placement.pendingFrom))return
      if(placement.watchdogTimer!==undefined)window.clearTimeout(placement.watchdogTimer)
      placement.watchdogTimer=undefined
      placement.pendingFrom=null
      runCaretPlacement()
    }
    const scheduleCaretPlacementCheck=()=>{
      const placement=caretPlacement
      if(!placement?.pendingFrom)return
      if(placement.settleTimer!==undefined)window.clearTimeout(placement.settleTimer)
      placement.settleTimer=window.setTimeout(()=>{
        if(caretPlacement!==placement)return
        placement.settleTimer=undefined
        checkCaretPlacement()
      },24)
    }
    const caretCursorMove=term.onCursorMove(scheduleCaretPlacementCheck)
    const caretWriteParsed=term.onWriteParsed(scheduleCaretPlacementCheck)
    const caretResize=term.onResize(()=>finishCaretPlacement('resized'))
    const startCaretPlacement=(requested:TerminalCaretPosition)=>{
      finishCaretPlacement('replaced')
      const resolveCaret=caretResolverForBackend(backendRef.current)
      if(replaying||paneIsHidden()||!resolveCaret)return false
      const resolved=resolveCaret(terminalCaretSnapshot(term),requested)
      if(!resolved)return false
      if(sameCaretPosition(resolved.current,resolved.target))return true
      // Like an explicit mobile Arrow key, placement invalidates the IME's textual
      // baseline: a later autocorrect replacement must not be computed across a cursor
      // jump in a draft the hidden bridge does not mirror.
      resetMobileInput()
      caretPlacement={
        targetColumn:resolved.target.column,
        targetRowOffset:resolved.target.row-resolved.promptRow,
        lastDirection:null,
        precision:false,
        keysSent:0,
        startedAt:performance.now(),
        pendingFrom:null,
        settleTimer:undefined,
        watchdogTimer:undefined,
      }
      diagnoseRender?.('caret_placement_started',{
        from:resolved.current,
        target:resolved.target,
        pointer:'terminal',
      })
      runCaretPlacement()
      return true
    }
    const keepMobileCaretAtEnd=()=>{
      if(!mobileLiveInput)return
      const end=mobileLiveInput.value.length
      mobileLiveInput.setSelectionRange(end,end)
    }
    const mobileBeforeInput=(event:InputEvent)=>{
      notePhysicalInput(event, 'beforeinput')
      if(event.inputType!=='insertLineBreak'&&event.inputType!=='insertParagraph')return
      event.preventDefault()
      if(!lineBreakSent)term.input(mobileLineBreak(),true)
      markLineBreakSent()
      resetMobileInput()
    }
    const mobileTextInput=(event:Event)=>{
      if(!mobileLiveInput)return
      if (!pendingInputCapture) notePhysicalInput(event, 'input')
      const next=mobileLiveInput.value
      if(lineBreakSent&&/^[\r\n]*$/.test(next)){
        resetMobileInput();return
      }
      const data=mobileImeDelta(mobileInputValue,next,mobileLineBreak())
      mobileInputValue=next
      if(data)term.input(data,true)
      else pendingInputCapture=null
      if(/[\r\n]/.test(next))resetMobileInput()
      else requestAnimationFrame(keepMobileCaretAtEnd)
    }
    const mobileKeyDown=(event:KeyboardEvent)=>{
      notePhysicalInput(event, 'keydown')
      const sequence:Record<string,string>={
        ArrowUp:'\x1b[A',ArrowDown:'\x1b[B',ArrowRight:'\x1b[C',ArrowLeft:'\x1b[D',
        Home:'\x1b[H',End:'\x1b[F',PageUp:'\x1b[5~',PageDown:'\x1b[6~',Delete:'\x1b[3~',
        Escape:'\x1b',Tab:'\t',
      }
      if(event.key==='Enter'&&!event.isComposing){
        event.preventDefault();term.input(mobileLineBreak(),true);markLineBreakSent();resetMobileInput();return
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
      if(image&&isAgentBackend(backendRef.current)){
        event.preventDefault();resetMobileInput()
        void attachFilesRef.current([image])
        return
      }
      const text=event.clipboardData.getData('text/plain')
      if(!text)return
      notePhysicalInput(event, 'paste')
      event.preventDefault();resetMobileInput();pasteIntoTerminal(term,session,text);focusTerminalInput()
    }
    mobileLiveInput?.addEventListener('beforeinput',mobileBeforeInput)
    mobileLiveInput?.addEventListener('input',mobileTextInput)
    mobileLiveInput?.addEventListener('keydown',mobileKeyDown)
    mobileLiveInput?.addEventListener('paste',mobilePaste)
    const selectionChange = term.onSelectionChange(() => {
      const text=term.getSelection()
      setSelectionText(text)
      if(text)cancelCaretPlacement()
      if(!text)lastAutoCopiedSelectionRef.current=''
    })
    const autoCopySelection=()=>{
      if(!mobileInput.autoCopySelection)return
      const text=term.getSelection()
      if(!text||text===lastAutoCopiedSelectionRef.current)return
      lastAutoCopiedSelectionRef.current=text
      captureCopy(text,'terminal')
      void copyPreparedText(text).then(copied=>{
        if(!copied)prepareClipboardFallback(text)
      })
    }
    let longPress: number | null = null
    let lastTouchAt = 0
    let activePointerId:number|null=null
    let tap:{
      pointerId:number
      pointerType:CaretPointerType
      startX:number
      startY:number
      px:number
      py:number
      moved:boolean
      primary:boolean
      modified:boolean
    }|null=null
    let touch:{
      pointerId:number
      lastY:number
      startX:number
      startY:number
      px:number
      py:number
      moved:boolean
      /** Sub-row scroll travel carried between move events. */
      pixels:number
      applicationInputPixels:number
      applicationReports:number
      /** Everything this gesture actually moved, so one that moved nothing can say so. */
      panPixels:number
      terminalSteps:number
      terminalMoved:boolean
      selecting:{start:TerminalCell;length:number}|null
    }|null=null
    // Focus (and the soft keyboard) is deferred to release: only a still tap sets this,
    // so a scroll or selection drag never raises the keyboard mid-gesture.
    let focusOnMouseClaim=false
    // What was holding the soft keyboard up when this touch landed, so a gesture that
    // turns out not to be typing can hand it back. Not raising the keyboard is only half
    // of leaving it alone: every touch here lands on non-editable content (the live input
    // is a 1px `pointer-events:none` bridge), and Android lowers the keyboard whenever a
    // touch resolves against something that is not the focused field. That is what took
    // the keyboard away mid long-press-and-drag, with nothing in this file asking for it.
    let softKeyboardBeforeGesture:HTMLElement|null=null
    // The dismissal count when this touch landed, so a restore can tell "the platform took
    // the keyboard" from "the user asked for it to go".
    let softKeyboardDismissalsBeforeGesture=0
    let forwardingTerminalMouse=false
    let selectionScrollTimer:number|undefined
    let selectionScrollDir=0
    const stopSelectionScroll=()=>{if(selectionScrollTimer!==undefined){window.clearInterval(selectionScrollTimer);selectionScrollTimer=undefined}selectionScrollDir=0}
    const cancelLongPress = () => { if (longPress !== null) window.clearTimeout(longPress); longPress = null }
    // One rendered row, in pixels: the unit both drag targets are measured in - the scroll
    // xterm's own viewport takes, and the scroll an application receives as whole wheel
    // reports. Falls back to xterm's default cell height for a pane with no element yet.
    const paneRowHeight = () => (term.element?.getBoundingClientRect().height??term.rows*13)/Math.max(term.rows,1)
    /**
     * Hand an application holding the mouse `rows` worth of scroll, one wheel event per row.
     *
     * The event count is the scroll: xterm turns each wheel event into exactly one scroll
     * report whatever magnitude it carries, so a single event asking for eight rows delivers
     * one. Line mode also steps around the pixel branch's own arithmetic, which divides by the
     * row height and then damps any delta under 50px to 30% of itself. A touch drag reports
     * 10-40px per move, so every one of them was being cut to a third before this.
     *
     * Bounded because the loop is driven by a measured row height: a pane mid-relayout can
     * report a hairline row, and no gesture means thousands of events.
     */
    const forwardApplicationScroll = (rows:number,clientX:number,clientY:number) => {
      const step = rows < 0 ? -1 : 1
      const count = Math.min(Math.abs(rows), APPLICATION_SCROLL_MAX_ROWS)
      for (let sent = 0; sent < count; sent++) {
        term.element?.dispatchEvent(new WheelEvent('wheel',{
          bubbles:true,cancelable:true,clientX,clientY,
          deltaY:step,deltaMode:WheelEvent.DOM_DELTA_LINE,
        }))
      }
    }
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
      cancelCaretPlacement()
      activePointerId=event.pointerId
      const pointerType:CaretPointerType=event.pointerType==='touch'||event.pointerType==='pen'?event.pointerType:'mouse'
      tap={
        pointerId:event.pointerId,
        pointerType,
        startX:event.clientX,
        startY:event.clientY,
        px:event.clientX,
        py:event.clientY,
        moved:false,
        primary:event.isPrimary&&event.button===0&&event.detail<=1,
        modified:event.altKey||event.ctrlKey||event.metaKey||event.shiftKey,
      }
      if (event.pointerType === 'touch') {
        lastTouchAt = Date.now()
        softKeyboardBeforeGesture=softKeyboardHolder()
        softKeyboardDismissalsBeforeGesture=softKeyboardDismissals()
        touch={
          pointerId:event.pointerId,lastY:event.clientY,startX:event.clientX,startY:event.clientY,
          px:event.clientX,py:event.clientY,moved:false,pixels:0,
          applicationInputPixels:0,applicationReports:0,
          panPixels:0,terminalSteps:0,terminalMoved:false,selecting:null,
        }
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
      if(forwardingTerminalMouse)return
      if(Date.now()-lastTouchAt>=1500)return
      // The synthesized tap after a drag (scroll/selection) is swallowed without
      // focusing, so only a genuine tap raises the soft keyboard.
      event.preventDefault();event.stopPropagation()
      if(focusOnMouseClaim)focusTerminalInput()
    }
    const pointerMove=(event:PointerEvent)=>{
      if(tap&&event.pointerId===tap.pointerId){
        tap.px=event.clientX;tap.py=event.clientY
        const threshold=tap.pointerType==='touch'?10:5
        if(!tap.moved&&Math.hypot(event.clientX-tap.startX,event.clientY-tap.startY)>threshold)tap.moved=true
      }
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
      const mouseActive=term.modes.mouseTrackingMode!=='none'
      const dragTarget=mobileDragTarget(mobileInput.verticalDrag,mouseActive)
      if(dragTarget==='disabled'){touch.lastY=event.clientY;return}
      // The keyboard covers part of a grid that cannot be shrunk to fit it, so this pane is a
      // window on a taller sheet — and a drag moves the window before it moves anything inside
      // it. Nearest content first: what the keyboard hides is the top of the *current* screen,
      // which is closer than the harness's own history, and on the alternate screen that
      // history cannot be reached by scrolling at all. Only the share of the finger the window
      // could not spend chains through to the scroll below, so a drag that runs the window to
      // its end keeps going into scrollback without a second gesture.
      const rawDy=event.clientY-touch.lastY
      const panInset=effectiveKeyboardInsetRef.current
      const panned=panInset>0?clampPeekOffset(peekOffsetRef.current+rawDy,panInset):peekOffsetRef.current
      const panMoved=panned-peekOffsetRef.current
      const panShare=rawDy===0?0:panMoved/rawDy
      if(panMoved!==0){
        event.preventDefault()
        touch.panPixels+=Math.abs(panMoved)
        setPeekOffsetValueRef.current(panned,false)
      }
      const delta=touchWheelDelta(touch.lastY,event.clientY,mobileInput)*(1-panShare)
      touch.lastY=event.clientY
      if(!delta)return
      // Claimed before the row check: a sub-row move is still part of this drag, and letting it
      // through would hand the browser a page scroll partway through a gesture.
      event.preventDefault()
      const rowHeight=paneRowHeight()
      if(dragTarget==='terminal'){
        const budget=terminalScrollSteps(touch.pixels+delta,rowHeight)
        touch.pixels=budget.remainder
        if(!budget.steps)return
        // Counted before the call, not after: `scrollLines` on a buffer with no scrollback
        // is a no-op, and the point of the tally is to tell "the pane asked for a scroll"
        // apart from "the pane got one".
        touch.terminalSteps+=Math.abs(budget.steps)
        const before=term.buffer.active.viewportY
        term.scrollLines(budget.steps)
        touch.terminalMoved=touch.terminalMoved||term.buffer.active.viewportY!==before
        return
      }
      const budget=applicationTouchScroll(
        {pixels:touch.pixels},delta,rowHeight,applicationTouchScrollProfile(backendRef.current),
      )
      touch.pixels=budget.remainder
      touch.applicationInputPixels+=Math.abs(delta)
      touch.applicationReports+=Math.abs(budget.steps)
      if(!budget.steps)return
      // This scroll belongs to the application, so nothing in xterm's buffer will ever record
      // it. The drags this pane forwards are its only evidence of where that viewport is, so
      // it totals them: a drag back through the history raises the chip, and a drag toward
      // the newest output spends the same total back down to zero and takes it away again.
      // Both halves matter - a reader who scrolls their own way back to the newest output is
      // otherwise left with a chip that only a tap can dismiss, on a viewport already sitting
      // exactly where the tap would send it. Totalled in the rows actually forwarded rather
      // than the finger's pixels, which is what the application moved by.
      appTailDistanceRef.current=trackAppTailDistance(appTailDistanceRef.current,budget.distance)
      const off=appOffTailByDistance(appTailDistanceRef.current,rowHeight)
      if(off!==appOffTailRef.current){
        appOffTailRef.current=off;setAppOffTail(off)
        recordTerminalRenderDiagnostic(session.id,'app_tail_estimate',{off,distance:appTailDistanceRef.current,rowHeight})
      }
      forwardApplicationScroll(budget.steps,event.clientX,event.clientY)
    }
    const forwardTerminalMouseTap=(clientX:number,clientY:number)=>{
      const screen=term.element?.querySelector<HTMLElement>('.xterm-screen')
      if(!screen)return false
      forwardingTerminalMouse=true
      syntheticMouseInputDepth+=1
      try{
        dispatchTerminalMouseTap(screen,clientX,clientY)
      }finally{
        syntheticMouseInputDepth-=1
        forwardingTerminalMouse=false
      }
      return true
    }
    const pointerEnd=(event:PointerEvent)=>{
      if(activePointerId!==event.pointerId)return
      activePointerId=null
      const endedTap=tap&&tap.pointerId===event.pointerId?tap:null
      tap=null
      // A quick, still tap means "type here" and raises the keyboard; a drag (scroll or
      // selection) does not. keyboardOff mode ignores the focus regardless.
      focusOnMouseClaim=event.pointerType==='touch'&&!!touch&&!touch.selecting&&!touch.moved
      if(endedTap){
        const action=terminalTapAction({
          backend:backendRef.current,
          pointerType:endedTap.pointerType,
          still:!endedTap.moved,
          primary:endedTap.primary,
          modified:endedTap.modified,
          readMode:keyboardOffRef.current||mobileDraftOpenRef.current,
          hasSelection:!!touch?.selecting||term.hasSelection(),
          mouseTracking:term.modes.mouseTrackingMode!=='none',
        })
        if(action==='forward-mouse')forwardTerminalMouseTap(endedTap.px,endedTap.py)
        if(action==='steer-caret'){
          const screen=term.element?.querySelector<HTMLElement>('.xterm-screen')
          if(screen){
            const requested=terminalCaretAtPoint(
              endedTap.px,endedTap.py,screen.getBoundingClientRect(),term.cols,term.rows,term.buffer.active.viewportY,
            )
            startCaretPlacement(requested)
          }
        }
      }
      if(focusOnMouseClaim)focusTerminalInput()
      if(touch?.applicationInputPixels){
        recordTerminalRenderDiagnostic(session.id,'mobile_application_scroll',{
          backend:backendRef.current,
          inputPixels:Math.round(touch.applicationInputPixels),
          reports:touch.applicationReports,
        })
      }
      // A deliberate vertical drag that moved nothing at all. Reported durably because the
      // symptom is unfalsifiable from the outside — "swiping does nothing" names no layer,
      // and the drag has four possible destinations (peek pan, xterm scrollback, a
      // forwarded wheel, or `disabled`) that all look identical when they fail. This says
      // which one it took and what the pane believed at the time, so the next occurrence
      // arrives as evidence rather than as another round of theories. Rate-limited to one
      // per second per session by the daemon.
      if(touch&&!touch.selecting){
        const travel=Math.abs(touch.py-touch.startY)
        const moved=touch.panPixels>0||touch.terminalMoved||touch.applicationReports>0
        if(travel>=MOBILE_DRAG_INERT_MIN_TRAVEL_PX&&!moved){
          const buffer=term.buffer.active
          reportInputDiagnostic('mobile_drag_inert',{
            backend:backendRef.current,
            target:mobileDragTarget(mobileInput.verticalDrag,term.modes.mouseTrackingMode!=='none'),
            mouseTracking:term.modes.mouseTrackingMode,
            bufferType:term.buffer.active.type,
            scrollback:buffer.length-term.rows,
            travelPx:Math.round(travel),
            terminalStepsAsked:touch.terminalSteps,
            keyboardInset:keyboardInsetRef.current,
            reserved:keyboardReservedRef.current,
            peekOffset:Math.round(peekOffsetRef.current),
            rowHeight:Math.round(paneRowHeight()),
          })
        }
      }
      stopSelectionScroll();cancelLongPress();touch=null;commitPeekOffsetRef.current()
      // A gesture that was not a typing tap gives the keyboard back exactly as it found
      // it — up if it was up, down if it was down. Deferred a frame, and ordered after
      // the copy, because the platform makes its own focus decision as the tap resolves:
      // restoring inside this handler would be undone by it. `focusOnMouseClaim` already
      // owns the other case, so the two never both act.
      const restoreKeyboard=focusOnMouseClaim?null:softKeyboardBeforeGesture
      const dismissalsAtStart=softKeyboardDismissalsBeforeGesture
      softKeyboardBeforeGesture=null
      requestAnimationFrame(()=>{autoCopySelection();restoreSoftKeyboard(restoreKeyboard,dismissalsAtStart)})
    }
    const pointerCancel=(event:PointerEvent)=>{
      if(activePointerId!==event.pointerId)return
      activePointerId=null;tap=null;focusOnMouseClaim=false;stopSelectionScroll();cancelLongPress();touch=null;commitPeekOffsetRef.current()
      // A cancelled pointer is the platform taking the gesture over, which is precisely
      // when it lowers the keyboard without anything here asking. Nothing typed, so the
      // keyboard state before the touch is the one to keep.
      const restoreKeyboard=softKeyboardBeforeGesture
      const dismissalsAtStart=softKeyboardDismissalsBeforeGesture
      softKeyboardBeforeGesture=null
      requestAnimationFrame(()=>restoreSoftKeyboard(restoreKeyboard,dismissalsAtStart))
    }
    const openMenu = (event: MouseEvent) => {
      // The terminal body has no context menu: right-click stays out of the
      // terminal surface entirely. Suppress the browser/desktop menu and the
      // touch long-press selection gesture, but never open our own menu here.
      event.preventDefault()
    }
    const pasteEvent = (event: ClipboardEvent) => {
      const data=event.clipboardData
      if (!data) return
      if (isAgentBackend(backendRef.current)) {
        const transferred=Array.from(data.files)
        const image = clipboardImage(Array.from(data.items))
        const files:Blob[]=transferred.length?transferred:image?[image]:[]
        if(files.length){
          event.preventDefault()
          event.stopPropagation()
          void attachFilesRef.current(files)
          return
        }
      }
      const text=data.getData('text/plain')
      if(!text)return
      notePhysicalInput(event, 'paste')
      claimTerminalTextPaste(event,value=>{
        pasteIntoTerminal(term,session,value)
      })
    }
    const hasFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types || []).includes('Files')
    const dragEnter = (event: DragEvent) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      if (isAgentBackend(backendRef.current) && !['exited','crashed'].includes(stateRef.current)) setFileDropActive(true)
    }
    const dragOver = (event: DragEvent) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = isAgentBackend(backendRef.current) && !['exited','crashed'].includes(stateRef.current) ? 'copy' : 'none'
      }
    }
    const dragLeave = (event: DragEvent) => {
      const next = event.relatedTarget
      if (next instanceof Node && host.current?.contains(next)) return
      setFileDropActive(false)
    }
    const drop = (event: DragEvent) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      event.stopPropagation()
      setFileDropActive(false)
      if (!isAgentBackend(backendRef.current)) {
        reportError('Drop files into an open agent session.')
        return
      }
      const files=Array.from(event.dataTransfer?.files||[])
      if (!files.length) {
        reportError('No readable files were dropped.')
        return
      }
      void attachFilesRef.current(files)
    }
    host.current.addEventListener('pointerdown', pointerClaim)
    host.current.addEventListener('mousedown',mobileMouseClaim,true)
    host.current.addEventListener('keydown', terminalKeyCapture, true)
    host.current.addEventListener('beforeinput', terminalBeforeInputCapture, true)
    window.addEventListener('pointermove', pointerMove)
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
    // The width-cap notice needs its own observer on the *track*, because the one
    // above watches the host and a capped host is precisely the one that stops
    // resizing. Separate rather than folded into `scheduleBurstFit` so a drag that the
    // cap absorbs costs two `clientWidth` reads instead of a fit proposal per frame:
    // by construction none of those frames can change the grid.
    const trackObserver = new ResizeObserver(() => judgeWidthCap())
    if (host.current.parentElement) trackObserver.observe(host.current.parentElement)
    const intersection = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) scheduleFullRedraw()
    })
    intersection?.observe(host.current)
    window.addEventListener('resize', scheduleBurstFit)
    // Redraw only: reconnect decisions all belong to the liveness watcher below, which
    // owns the attempt bookkeeping and can also recover from a stalled handshake.
    // Scheduled in both directions: becoming hidden is what deregisters this pane's
    // viewport, so the PTY stops being sized for a window nobody is looking at.
    const onVisibility=()=>{if(paneIsHidden()){cancelCaretPlacement();scheduleFit()}else{flushOutputAck();scheduleFullRedraw()}}
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
    return () => { disposed=true;finishCaretPlacement('disposed');stopSelectionScroll();stopLivenessWatch();stopInputStallWatch();clearHandshakeWatchdog();reconnectNowRef.current=()=>{};if(reconnectTimer!==undefined)clearTimeout(reconnectTimer);if(replyRefreshTimer!==undefined)clearTimeout(replyRefreshTimer);if(lineBreakResetTimer!==undefined)clearTimeout(lineBreakResetTimer);if(outputAckTimer!==undefined)clearTimeout(outputAckTimer);window.clearInterval(terminalStateTimer);if(keyboardSettleTimer!==undefined)window.clearTimeout(keyboardSettleTimer);scheduleKeyboardSettleRef.current=()=>{};bufferChange.dispose();tailScroll.dispose();tailRender.dispose();writeParsed.dispose();renderDiagnostic?.dispose();input.dispose();wheelPacer.dispose();selectionChange.dispose();caretCursorMove.dispose();caretWriteParsed.dispose();caretResize.dispose();cancelLongPress();observer.disconnect();trackObserver.disconnect();intersection?.disconnect();window.cancelAnimationFrame(fitFrame);window.cancelAnimationFrame(redrawFrame);surfaceRepair?.cancel();window.cancelAnimationFrame(visibilityFrame);window.clearTimeout(surfaceConfirmTimer);window.removeEventListener('resize',scheduleBurstFit);window.visualViewport?.removeEventListener('resize',scheduleBurstFit);viewportScheduler.cancel();document.removeEventListener('visibilitychange',onVisibility);window.removeEventListener('pageshow',onPageShow);window.removeEventListener('focus',onWindowFocus);window.removeEventListener('error',onRenderError);window.removeEventListener('pointermove',pointerMove);window.removeEventListener('pointerup',pointerEnd);window.removeEventListener('pointercancel',pointerCancel);window.removeEventListener('mux:theme',onTheme);window.removeEventListener(PRESENCE_REPORTED_EVENT,onPresenceReported);mobileLiveInput?.removeEventListener('beforeinput',mobileBeforeInput);mobileLiveInput?.removeEventListener('input',mobileTextInput);mobileLiveInput?.removeEventListener('keydown',mobileKeyDown);mobileLiveInput?.removeEventListener('paste',mobilePaste);if(mobileLiveInput)mobileLiveInput.value='';host.current?.removeEventListener('pointerdown',pointerClaim);host.current?.removeEventListener('mousedown',mobileMouseClaim,true);host.current?.removeEventListener('keydown',terminalKeyCapture,true);host.current?.removeEventListener('beforeinput',terminalBeforeInputCapture,true);host.current?.removeEventListener('focusin',claimOnFocus);host.current?.removeEventListener('contextmenu',openMenu);host.current?.removeEventListener('paste',pasteEvent,true);host.current?.removeEventListener('dragenter',dragEnter);host.current?.removeEventListener('dragover',dragOver);host.current?.removeEventListener('dragleave',dragLeave);host.current?.removeEventListener('drop',drop);if(socket){socket.onclose=null;socket.close()}term.dispose();termRef.current=null;searchRef.current=null;focusTerminalInputRef.current=()=>{};pasteAttachmentRef.current=()=>{};claimInputRef.current=()=>{};resizeToPaneRef.current=()=>{};applyBaseFontRef.current=()=>{} }
  }, [session.id, keybindings, scrollback, rendererPreference, windowsPty, mobileInput, remountEpoch])

  // Every Action rail button, including the fixed mobile Send end-cap, preserves an
  // already-open keyboard through its press. RailScroller owns the same guard for its
  // scrolling rows; handling the full toolbar here closes the sibling end-cap gap.
  useEffect(()=>{
    const surface=host.current?.closest<HTMLElement>('.terminal-surface')
    if(!surface)return
    const preserveRailKeyboard=(event:MouseEvent)=>{
      if(event.target instanceof Element&&event.target.closest('.terminal-action-rail'))holdSoftKeyboard(event)
    }
    surface.addEventListener('mousedown',preserveRailKeyboard)
    return()=>surface.removeEventListener('mousedown',preserveRailKeyboard)
  },[session.id])

  // Its own effect on purpose: the one above owns the terminal's whole lifetime, so
  // listing the scale in its deps would dispose the terminal, drop the socket and
  // replay the entire buffer to change a number xterm takes directly.
  useEffect(() => { applyBaseFontRef.current() }, [uiScale])

  const copy = async () => {
    const term = termRef.current
    if (!term?.hasSelection()) {
      const problem = 'Copy requires a terminal selection.'
      reportError(problem)
      setMenu(null)
      throw new Error(problem)
    }
    const text = term.getSelection()
    // Reported here for the provenance label; the global capture hook would
    // otherwise record the same text as a generic 'app' copy (and is deduped).
    captureCopy(text, 'terminal')
    if (await copyPreparedText(text)) {
      term.clearSelection()
      setPreparedClipboard('')
      setManualClipboard(false)
    } else {
      prepareClipboardFallback(text)
      setMenu(null)
      throw new Error('Clipboard access was blocked. Manual copy is ready in the terminal.')
    }
    setMenu(null)
  }
  const paste = async (textOnly=false) => {
    const term = termRef.current
    if(!term)throw new Error('The target terminal is not mounted.')
    try {
      const pasted=textOnly
        ?(pasteIntoTerminal(term,session,await navigator.clipboard.readText()),'text' as const)
        :await pasteBrowserClipboard(term, session, files=>attachFilesRef.current(files))
      focusAfterTerminalActionRef.current()
      if(pasted==='text')showClipboardStatus('Pasted')
    } catch {
      setManualPaste(true)
      requestAnimationFrame(()=>manualPasteRef.current?.focus())
      throw new Error('Clipboard access was blocked. Manual paste is open in the terminal.')
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
      setPreparedClipboard('');setManualClipboard(false)
    }else prepareClipboardFallback(text)
  }

  // ---- the unsent draft ----------------------------------------------------
  //
  // Read off the cell grid, because nothing else can answer it: no harness
  // publishes its composer, and the daemon's write log deliberately keeps a count
  // rather than text (`composerText.ts` says why at length). Three outcomes, and
  // they must not be collapsed into two — `null` is "this screen is not showing a
  // readable draft", which is a different thing from an empty one.
  const readComposer = ():string|null => {
    const term=termRef.current
    if(!term||!composerIsReadable(session.backend))return null
    return readComposerText(session.backend,terminalCaretSnapshot(term))
  }
  const composerUnreadable = 'The composer could not be read from this screen. It may be showing a dialog, a picker, or output rather than a draft.'
  const copyComposerInput = async () => {
    const text=readComposer()
    if(text===null){reportError(composerIsReadable(session.backend)?composerUnreadable:`Reading the composer is not implemented for ${harnessDisplayName(session.backend)} sessions.`);return}
    if(!text){showClipboardStatus('The composer is empty');return}
    captureCopy(text,'composer')
    if(await copyPreparedText(text)){
      setPreparedClipboard('');setManualClipboard(false)
      showClipboardStatus(`Copied ${text.length.toLocaleString()} chars`)
    }else prepareClipboardFallback(text)
  }
  // There is deliberately no Clear counterpart to the copy above. It sent the
  // harness's declared whole-composer discard sequence, and for Claude that
  // sequence is a double Esc, which interrupts a running turn — so a button
  // labelled as tidying a draft could abort work. The rail carries a plain Ctrl+U
  // key instead (`ctrlU` in `commandRail.ts`), which is honest about killing only
  // to the start of the line. `composerClearSequence` stays measured and published
  // for `composer_input.py`'s unsent-input accounting, which still needs to know
  // which write discards a draft.
  /** Answer the approval this pane is showing, once.
   *
   *  Routed through the daemon rather than by writing Enter here, because the
   *  server is the only side that can re-check what the click is answering: the
   *  same agent run, this session's own screen still classifying as an approval,
   *  and the same prompt fingerprint. A pane that wrote `\r` itself would send a
   *  bare Enter into whatever the screen had become in the meantime — the
   *  composer, a menu, or a different dialog. */
  const approveOnce = async () => {
    if(approveBusy)return
    setApproveBusy(true)
    try {
      const result=await api<{operation:string}>('POST',`/api/sessions/${session.id}/approvals/approve-once`)
      showClipboardStatus(`Approved ${result.operation}`)
    } catch(cause) {
      reportError(cause instanceof Error?cause.message:'The approval could not be answered.')
    } finally {
      setApproveBusy(false)
    }
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
  // The find bar is the pane's only dismissable state. It matters most on a phone, where
  // there is no Escape key at all and the platform back gesture is the way out of it.
  useDismissLevel(closeFind, findOpen, 'terminal-find')

  useEffect(() => {
    const onAction = (event: Event) => {
      const detail = (event as CustomEvent<{sessionId:string|null;action:string;text?:string;submit?:boolean;requestId?:string}>).detail
      if (detail.sessionId !== session.id) return
      const perform = (operation:()=>void|Promise<void>) => {
        void Promise.resolve().then(operation).then(()=>{
          if(detail.requestId)window.dispatchEvent(new CustomEvent('mux:terminal-action-result',{detail:{requestId:detail.requestId,ok:true}}))
        }).catch(cause=>{
          if(detail.requestId)window.dispatchEvent(new CustomEvent('mux:terminal-action-result',{detail:{requestId:detail.requestId,ok:false,error:cause instanceof Error?cause.message:String(cause)}}))
        })
      }
      if (detail.action === 'copy') perform(copy)
      else if (detail.action === 'paste') perform(()=>paste())
      else if (detail.action === 'pasteText') perform(()=>paste(true))
      else if (detail.action === 'selectAll') { termRef.current?.selectAll(); setMenu(null) }
      else if (detail.action === 'clear') { termRef.current?.clear(); setMenu(null) }
      else if (detail.action === 'find') find()
      else if (detail.action === 'toggleKeyboard') cycleMobileInputMode()
      else if (detail.action === 'insertText' && detail.text) perform(()=>injectText(detail.text!, detail.submit))
      // External action surfaces route key writes here rather than touching xterm:
      // the pane stays the single owner of terminal writes, so broadcast, replay,
      // and read/select mode still apply.
      else if (detail.action === 'sendKey' && detail.text) perform(()=>{
        if(!termRef.current)throw new Error('The target terminal is not mounted.')
        sendKey(detail.text!)
      })
      else if (detail.action === 'copyReply') void copyLastReply()
      else if (detail.action === 'copyInput') void copyComposerInput()
      else if (detail.action === 'copyResume') void copyResumeCommand()
      else if (detail.action === 'branch') onBranch?.()
      else if (detail.action === 'approveOnce') void approveOnce()
      else if (detail.action === 'attach' && acceptsTerminalAttachments(session)) attachmentInputRef.current?.click()
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
  // Their focus restoration preserves a visible keyboard and leaves a dismissed one down;
  // read/select and Draft modes do not refocus at all.
  // xterm already means to put the viewport back on the tail here (`scrollOnUserInput`), but
  // its own attempt is the single clamped call `scrollTerminalToTail` exists to finish.
  const sendKey=(sequence:string)=>{
    termRef.current?.input(sequence,true)
    scrollTerminalToTail(termRef.current)
    // The rail's own `^End` reaches the same place the chip does, so it takes the chip down
    // with it. Only this one sequence needs saying here: `sequence` reaches the CLI through
    // `input()` and therefore through `onData`, where a submission already drops the estimate
    // (`inputResetsAppTail`), and a view key is not typing and never goes that way.
    if(sequence===APP_TAIL_KEY)clearAppTail('rail_tail_key')
    if(!keyboardOffRef.current&&!mobileDraftOpenRef.current)focusAfterTerminalActionRef.current()
  }
  // Sticky Ctrl/Alt/Shift for the rail's key chips (`railModifiers.ts`).
  //
  // The modified bytes are resolved where a chip is *rendered*, not where it is sent, and
  // that is load-bearing rather than incidental: both repeating paths capture what they
  // are repeating when the press opens - `railKeyRepeat` stores the sequence, `RailPad`
  // closes over the slot's handler - so consuming an armed modifier on the first send
  // cannot pull it out from under the rest of the hold. Sending would re-read the state
  // and drop the modifier from the second repetition onwards.
  const [railModifiers,setRailModifiers]=useState<RailModifierState>(EMPTY_RAIL_MODIFIERS)
  const activeModifiers=activeRailModifiers(railModifiers)
  const modifierPrefix=railModifierPrefix(activeModifiers)
  // Arming a modifier and then switching session would leave it applying to a pane the
  // operator never armed it on.
  useEffect(()=>{setRailModifiers(EMPTY_RAIL_MODIFIERS)},[session.id])
  // Only a key sequence consumes an arm. A skill, a prompt or a picker has no notion of
  // Ctrl, so swallowing the modifier there would make it vanish for no visible reason.
  const sendRailKey=(sequence:string)=>{
    sendKey(sequence)
    setRailModifiers(current=>consumeRailModifiers(current))
  }
  const modifiedSequence=(item:RailItem)=>applyRailModifiers(item.bytes||'',activeModifiers)
  const railKeyRepeat=useRailKeyRepeat(sendRailKey,session.id)
  const railPadControl=useRailPad(session.id)
  // Jump-to-latest, for both viewports rather than only xterm's (see `appOwnsTail`). Scrolling
  // the terminal alone is what left this dead on a phone in a Claude session while the rail's
  // `^End` — which happens to send the same key on its way past — kept working.
  //
  // Moves the viewport and nothing else: it raises no keyboard (it never focuses) and lowers
  // none either (the chip's `holdSoftKeyboard` keeps the press from taking focus off the live
  // input). The chip is a sibling of the terminal host rather than a child, so the host's own
  // `mobileMouseClaim` guard never covered it — which is why reaching the newest line used to
  // close the keyboard and resize the pane out from under the line it had just jumped to.
  const jumpToLatest=()=>{
    const term=termRef.current
    // A pane that has been forwarding drags counts as much as a live mouse mode: whatever the
    // application has since done with the mouse, it is the thing that scrolled.
    const appReceivesScroll=appOffTailRef.current||term?.modes.mouseTrackingMode!=='none'
    if(term&&appOwnsTail(session.backend,appReceivesScroll))sendViewKeyRef.current(APP_TAIL_KEY)
    scrollTerminalToTail(term)
    clearAppTail('jump_to_latest')
  }
  const setMobileInputMode=(mode:MobileTerminalInputMode)=>{
    const draftOpen=mode==='draft'
    const keyboardOff=mode==='read'
    mobileDraftOpenRef.current=draftOpen
    setMobileDraftOpen(draftOpen)
    keyboardOffRef.current=keyboardOff
    setKeyboardOff(keyboardOff)
    if(mode==='read'||mode==='draft'){
      mobileTypingIntentRef.current=false
      if(mobileLiveInputRef.current)mobileLiveInputRef.current.inputMode='none'
    }
    if(mode==='read')mobileLiveInputRef.current?.blur()
    if(mode==='draft')setMobileDraftError('')
    if(mode==='live')requestAnimationFrame(()=>focusTerminalInputRef.current())
  }
  const cycleMobileInputMode=()=>{
    const current=mobileTerminalInputMode(keyboardOffRef.current,mobileDraftOpenRef.current)
    setMobileInputMode(nextMobileTerminalInputMode(current,acceptsTerminalAttachments(session)))
  }
  const closeMobileDraft=()=>setMobileInputMode('live')
  const setMobileDraftText=(text:string)=>{
    const kept=mobileTerminalDraftStore.set(session.id,text)
    mobileDraftTextRef.current=kept
    setMobileDraftTextState(kept)
    setMobileDraftError('')
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
      setPreparedClipboard('');setManualClipboard(false)
    } else prepareClipboardFallback(resumeCmd)
  }

  const retryPreparedCopy=async()=>{
    if(!preparedClipboard)return
    if(await copyPreparedText(preparedClipboard,manualClipboardRef.current)){
      setPreparedClipboard('');setManualClipboard(false);return
    }
    setManualClipboard(true)
    requestAnimationFrame(()=>{manualClipboardRef.current?.focus();manualClipboardRef.current?.select()})
  }

  // Inject literal text (skills, slash commands, custom macros) then optionally
  // submit with Enter, mirroring the raw onData path used by sendKey.
  //
  // This is the single chokepoint every insert path funnels through - prompt
  // templates, skills, clipboard entries, voice append, the mobile draft, the
  // branch seed - so the dialog refusal lives here and covers all of them at once.
  const injectText=async(text:string,submit?:boolean):Promise<void>=>{
    if(!text)return
    const refusal=insertionRefusal(session)
    if(refusal)throw new Error(refusal)
    const target=termRef.current
    if(!target)throw new Error('The target terminal is not ready. Draft kept.')
    await settleTerminalInsertion(
      text,
      !!submit,
      value=>pasteIntoTerminal(target,session,value),
      ()=>{
        if(termRef.current!==target)throw new Error('The target terminal changed before submit. Draft kept.')
        sendKey('\r')
      },
    )
    if(!submit&&!keyboardOffRef.current&&!mobileDraftOpenRef.current)focusAfterTerminalActionRef.current()
  }

  const insertMobileDraft=async()=>{
    const text=mobileDraftTextRef.current
    const targetSessionId=session.id
    if(!text||mobileDraftInsertingRef.current)return
    const insertGeneration=++mobileDraftInsertGenerationRef.current
    mobileDraftInsertingRef.current=true
    setMobileDraftInserting(true)
    setMobileDraftError('')
    try{
      // Draft insertion is verbatim bracketed paste only. Never append Enter here:
      // multiline text and its surrounding whitespace must remain in the agent composer.
      await insertMobileTerminalDraft(text,injectText)
      mobileTerminalDraftStore.set(targetSessionId,'')
      if(activeSessionIdRef.current===targetSessionId){
        mobileDraftTextRef.current=''
        setMobileDraftTextState('')
        setMobileInputMode('live')
      }
    }catch(cause){
      const message=cause instanceof Error?cause.message:String(cause)
      if(activeSessionIdRef.current===targetSessionId)setMobileDraftError(message)
      reportError(message)
    }finally{
      if(activeSessionIdRef.current===targetSessionId&&mobileDraftInsertGenerationRef.current===insertGeneration){
        mobileDraftInsertingRef.current=false
        setMobileDraftInserting(false)
      }
    }
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
    if(!button||button.disabled||!rail.contains(button)||button.classList.contains('overflow-rail-edge'))return
    button.classList.remove('rail-pulse')
    void button.offsetWidth
    button.classList.add('rail-pulse')
    const clear=()=>{button.classList.remove('rail-pulse');button.removeEventListener('animationend',clear)}
    button.addEventListener('animationend',clear)
  }

  const runPromptItem=async(item:RailItem)=>{
    const result=await activatePromptRailItem(item,{sessionId:session.id,projectId:session.project_id})
    if(result.status==='error')reportError(result.message)
  }

  // The rail region after the leading voice chips is data-driven so it can be
  // reordered/extended from settings; built-in ids keep their exact dynamic
  // markup (disabled states, tooltips), generic types render uniformly.
  //
  // Strip surface only, and one scroller per configured row: the rows come from
  // *this device's* layout, so a phone's rail is arranged independently of the
  // desktop's rather than sharing one order. The permanent row drawer exposes the
  // complete list when the visible strip is horizontally constrained.
  const mobilePinnedSend=isMobileTerminalInput()&&mobileEnterNeedsPinnedSend(session.backend)
  const mobileInputModeState=mobileTerminalInputMode(keyboardOff,mobileDraftOpen)
  const mobileInputModeIcon=mobileInputModeState==='live'?'⌨':mobileInputModeState==='read'?'↕':'✎'
  const nextInputMode=nextMobileTerminalInputMode(mobileInputModeState,acceptsTerminalAttachments(session))
  const mobileInputModeTitle=mobileInputModeState==='live'
    ?'Live input. Tap for read/select mode.'
    :mobileInputModeState==='read'
      ?acceptsTerminalAttachments(session)?'Read/select mode. Tap for persistent Draft.':'Read/select mode. Tap for live input.'
      :'Persistent Draft. Tap for live input; text stays saved.'
  const railConfig=loadRailConfig(session.project_id)
  // Kept beside the rows because a pad's slots name catalog ids rather than carrying
  // their own behaviour, so resolving one means a lookup the rows themselves do not do.
  const railItems=new Map(railConfig.items.map(entry=>[entry.id,entry]))
  const railRows=resolveRailRows(railConfig,'strip',{device:currentProfile(),backend:session.backend as RailBackend})
  // Only a rail that actually carries an auto-labelled prompt button reads the
  // library; every other rail costs nothing (`promptTitles.ts`).
  const promptTitles=usePromptTitles(session.project_id,railRows.some(row=>row.entries.some(entry=>entry.item.type==='prompt'&&entry.item.autoLabel)))
  // Mobile agent Enter is reserved for composing a newline. The ordinary Enter
  // item therefore moves to the fixed end-cap instead of remaining as a second,
  // scrollable submit target. The fixed action is intentionally not configurable:
  // removing it would strand a phone whose soft-keyboard Enter cannot submit.
  const scrollingRailRows=mobilePinnedSend
    ?railRows.map(row=>({...row,entries:row.entries.filter(entry=>entry.item.id!=='enter')})).filter(row=>row.entries.length)
    :railRows
  // One drop-up at a time, and a second tap on its own trigger closes it — the
  // trigger is the affordance, so it has to be able to undo itself.
  const toggleDropup=(kind:RailDropupState['kind'],anchor:HTMLElement)=>
    setDropup(current=>current?.kind===kind?null:{kind,anchor})
  const actionPresentation=(item:RailItem,fallback=item.label)=>{
    const hasIcon=railItemHasIcon(item.id)
    const mode=railItemDisplayMode(item,hasIcon)
    const label=railItemDisplayLabel(item,fallback)
    return {
      className:mode==='icon'?'rail-icon':mode==='icon-label'?'rail-icon rail-icon-label':undefined,
      content:mode==='label'?label:mode==='icon'?<RailItemIcon id={item.id}/>:<><RailItemIcon id={item.id}/><span class="rail-icon-label-text">{label}</span></>,
    }
  }
  // What a rail item *does*, separated from how it is drawn.
  //
  // One resolver rather than a switch that builds buttons, because a pad slot has to run
  // an action without rendering its chip, and gating answered in two places is gating that
  // drifts. `renderRailItem` builds the chip out of this; `RailPad` calls `run` from a
  // direction and reads `disabled` for a dead one. `null` is an item this session does not
  // have at all, which is a hidden chip and a dead direction.
  //
  // `run` takes the element it was activated from, because the three pickers anchor a
  // drop-up to it - from a pad that is the pad's own chip, which is where the drop-up
  // should hang from anyway.
  type RailItemView={
    run:(anchor:HTMLElement|null)=>void
    className?:string
    content?:ComponentChildren
    title?:string
    ariaLabel?:string
    disabled?:boolean
    expanded?:boolean
    /** Short face for a pad petal, where the chip's icon and full wording do not fit. */
    padLabel?:string
  }
  const railItemView=(item:RailItem):RailItemView|null=>{
    switch(item.id){
      case 'attach':{
        if(!acceptsTerminalAttachments(session))return null
        const view=actionPresentation(item)
        return {...view,run:()=>attachmentInputRef.current?.click(),disabled:attachmentBusy||!canInsertTerminalAttachment(session.state,attachmentReady),ariaLabel:attachmentBusy?'Attaching files':'Attach files',title:attachmentBusy?'Attaching files…':!attachmentReady?'Terminal restoring…':item.title||'Attach files to this chat without sending'}
      }
      case 'relaunch':{
        if(!isTask)return null
        const view=actionPresentation(item)
        return {...view,className:`term-relaunch ${view.className||''}`.trim(),run:()=>runCommand('session.relaunch'),title:'Relaunch this task terminal - stops it and re-runs the same command'}
      }
      case 'copyReply':{
        if(isTask)return null
        const view=actionPresentation(item)
        return {...view,run:()=>void copyLastReply(),disabled:!isAgentBackend(session.backend),ariaLabel:'Copy last reply',title:!isAgentBackend(session.backend)?'Copy reply is available in agent sessions':'Copy the latest assistant reply',padLabel:'Reply'}
      }
      case 'copyResume':{
        if(isTask)return null
        const view=actionPresentation(item)
        return {...view,run:()=>void copyResumeCommand(),disabled:!resumeCmd,title:resumeCmd?`Copy “${resumeCmd}” to resume this conversation in any terminal${resolvesTranscriptByCwd(session.backend)?` (run it from ${session.run_cwd||session.cwd})`:''}`:isAgentBackend(session.backend)&&!assignsConversationId(session.backend)?`${harnessDisplayName(session.backend)} has not reported its session id yet`:'Resume commands are available in agent sessions',padLabel:'Resume'}
      }
      case 'branch':{
        if(!onBranch)return null
        // Reads the daemon's declared strategy rather than restating which harnesses
        // have one. The server refuses anything else with `branch_unsupported`; this
        // gate just says so before the click. A harness whose conversation id mux
        // minted is branchable immediately; the rest wait for a discovered id.
        const branchable=supportsBranch(session.backend)
        const ready=branchable&&(assignsConversationId(session.backend)||(!!session.native_session_id&&session.native_session_id!==session.id))
        const view=actionPresentation(item)
        return {...view,run:()=>onBranch(),disabled:!ready,ariaLabel:'Branch this conversation',title:ready?'Fork this conversation into a sibling pane, keeping the original open':branchable?`${harnessDisplayName(session.backend)} has not reported its session id yet - branch is available shortly`:`Branching is not implemented for ${harnessDisplayName(session.backend)} sessions`}
      }
      case 'approveOnce':{
        // Enabled only while this session is actually showing an approval. The
        // server re-checks the same thing plus the prompt fingerprint, so this
        // gate is a readability affordance rather than the guard: a button that
        // is always clickable and usually 409s teaches nothing.
        const showing=session.state==='awaiting'&&session.awaiting_reason==='approval'
        const view=actionPresentation(item)
        return {...view,className:`rail-approve ${view.className||''}`.trim(),content:approveBusy?'…':view.content,run:()=>void approveOnce(),disabled:!showing||approveBusy,ariaLabel:'Approve the pending request',title:showing?'Approve the request this session is showing':'Available while this session is waiting for an approval'}
      }
      case 'paste':{
        const view=actionPresentation(item)
        return {...view,run:()=>void paste(),ariaLabel:'Paste into terminal',title:'Paste the clipboard into this terminal'}
      }
      // The two pickers open a drop-up of the most recent/relevant few rather than
      // the drawer section outright. The section is still one tap away, from the
      // sticky row inside the drop-up, so nothing became less reachable.
      case 'clipboardHistory':{
        const view=actionPresentation(item)
        return {...view,className:`${dropup?.kind==='clipboard'?'rail-dropup-open-trigger ':''}${view.className||''}`.trim()||undefined,expanded:dropup?.kind==='clipboard',run:anchor=>{if(anchor)toggleDropup('clipboard',anchor)},title:'Recent clipboard - tap an entry to insert it here',padLabel:'Clip'}
      }
      // `agentOnly` already keeps this off shells, where the endpoint 409s.
      case 'skills':{
        const view=actionPresentation(item)
        return {...view,className:`${dropup?.kind==='skills'?'rail-dropup-open-trigger ':''}${view.className||''}`.trim()||undefined,expanded:dropup?.kind==='skills',run:anchor=>{if(anchor)toggleDropup('skills',anchor)},title:item.title||'Insert one of this session’s skills'}
      }
      // Every template, not only those with dedicated configured buttons.
      case 'prompts':{
        const view=actionPresentation(item)
        return {...view,className:`${dropup?.kind==='prompts'?'rail-dropup-open-trigger ':''}${view.className||''}`.trim()||undefined,expanded:dropup?.kind==='prompts',run:anchor=>{if(anchor)toggleDropup('prompts',anchor)},title:item.title||'Insert one of your prompt templates'}
      }
      case 'copyInput':{
        // Disabled rather than absent on an unmeasured harness: a button that is
        // simply missing reads as "not built", while one that says why reads as
        // "not here yet", which is the true answer.
        const readable=composerIsReadable(session.backend)
        const view=actionPresentation(item)
        return {...view,run:()=>void copyComposerInput(),disabled:!readable,ariaLabel:'Copy composer text',title:readable?item.title||'Copy the text sitting unsent in this composer':`Reading the composer is not implemented for ${harnessDisplayName(session.backend)} sessions`,padLabel:'Input'}
      }
      case 'actionsDrawer':{
        const view=actionPresentation(item)
        return {...view,run:()=>runCommand('drawer.peekActions'),title:item.title}
      }
      case 'endSession':{
        // Ended sessions keep the button: the same command removes their row from the
        // sidebar, which is the only remaining thing left to do with them.
        const ended=session.state==='exited'||session.state==='crashed'
        const verb=ended?'Remove':'End session'
        const view=actionPresentation(item,ended?'Remove':item.label)
        return {...view,className:`rail-danger ${killArmed?'confirming ':''}${view.className||''}`.trim(),content:killArmed?'Confirm ✓':view.content,run:()=>runCommand('session.kill'),ariaLabel:killArmed?`Confirm ${verb.toLowerCase()}`:verb,title:killArmed?'Click again to confirm':ended?'Remove this ended session from the sidebar (click twice to confirm)':'End this session (click twice to confirm)'}
      }
      case 'kbdToggle':return {
        className:`term-key kbd-toggle mode-${mobileInputModeState} ${mobileDraftText?'has-draft':''}`,
        content:railItemDisplayLabel(item,mobileInputModeIcon),
        run:()=>cycleMobileInputMode(),
        ariaLabel:`Mobile input mode: ${mobileInputModeState}. Tap for ${nextInputMode}.`,
        title:mobileInputModeTitle,
      }
    }
    const modifier=railModifierForItem(item.id)
    if(modifier){
      // Three states in one control, and the phase is on the class rather than in the
      // label so the chip's width never changes as it is armed and locked.
      const phase=railModifierPhase(railModifiers,modifier)
      const view=actionPresentation(item)
      return {...view,className:`${item.className||'term-key'} rail-modifier-${phase}`,run:()=>setRailModifiers(current=>toggleRailModifier(current,modifier)),ariaLabel:`${item.label}: ${phase==='off'?'off':phase==='armed'?'armed for the next key':'locked'}`,title:phase==='locked'?`${item.label} is locked. Tap to clear.`:phase==='armed'?`${item.label} applies to the next key. Tap to lock.`:item.title}
    }
    if(item.type==='key'){
      const label=railItemDisplayLabel(item)
      const sequence=modifiedSequence(item)
      return {className:item.className||'term-key',content:label,run:()=>sendRailKey(sequence),title:`${modifierPrefix?`${modifierPrefix}+`:''}${item.title||label}`,padLabel:label}
    }
    if(item.type==='action'||item.type==='pad')return null
    // Prompt templates resolve over the network at click time (see promptRail.ts), so
    // they cannot go through the synchronous payload path below.
    // `rail-text` on the four configured types and on none of the built-ins: their labels are
    // whatever the user named the thing, so they size to their own text, while the built-in
    // wording is fixed and its even widths are the row's rhythm (see style.css).
    if(item.type==='prompt'){
      const label=railItemDisplayLabel(item,railItemLabel(item,promptTitles))
      return {className:`rail-text ${item.className||''}`.trim(),content:label,padLabel:label,run:()=>void runPromptItem(item),title:item.title||'Insert this prompt template into the composer'}
    }
    const payload=railPayload(item,session.backend as RailBackend)
    return {
      className:`rail-text ${item.className||''}`.trim(),
      content:railItemDisplayLabel(item),
      padLabel:railItemDisplayLabel(item),
      run:()=>{void injectText(payload,item.submit).catch(cause=>reportError(cause instanceof Error?cause.message:String(cause)))},
      title:item.title||payload,
    }
  }
  // The bound directions of a pad, resolved against this session. A slot naming an item
  // the session does not have, or that its backend filters out, stays in place as a
  // *disabled* direction rather than being dropped: directions are positional, so a pad
  // that rearranged itself per backend would be worse than one with a dead corner.
  const railPadSlots=(item:RailItem):RailPadSlotView[]=>{
    const pad=item.pad
    if(!pad)return []
    const views:RailPadSlotView[]=[]
    for(const key of padSlotKeys(pad.orientation)){
      const slot=pad.slots[key]
      if(!slot)continue
      const target=railItems.get(slot.item)
      const view=target&&railItemVisible(target,session.backend as RailBackend)?railItemView(target):null
      const label=view?.padLabel||view?.title||target?.label||slot.item
      views.push({
        key,
        itemId:slot.item,
        label:railItemDisplayLabel(target||{id:slot.item,type:'text',label},label),
        title:view?.title||label,
        mode:railPadSlotMode(slot,target),
        disabled:!view||!!view.disabled,
        run:anchor=>view?.run(anchor),
      })
    }
    return views
  }
  const renderRailItem=({item,key}:RailEntry)=>{
    if(item.type==='pad'){
      const slots=railPadSlots(item)
      // A pad every one of whose slots this session filtered out is not a control, it is
      // a chip that does nothing - the same reason the mutually exclusive built-ins render
      // as nothing rather than as dead buttons.
      if(!slots.some(slot=>!slot.disabled))return null
      const view=actionPresentation(item)
      return <RailPad key={key} controller={railPadControl} item={item} slots={slots} className={`${item.className||'term-key'} ${view.className||''}`.trim()} content={view.content} modifierPrefix={modifierPrefix||undefined}/>
    }
    if(item.type==='key'&&isRepeatableRailKey(item.id)){
      const label=railItemDisplayLabel(item)
      return <RailRepeatKey key={key} repeat={railKeyRepeat} sequence={modifiedSequence(item)} label={label} title={`${modifierPrefix?`${modifierPrefix}+`:''}${item.title||label}`} className={item.className||'term-key'}/>
    }
    const view=railItemView(item)
    if(!view)return null
    return <button
      key={key}
      class={view.className}
      disabled={view.disabled}
      aria-label={view.ariaLabel}
      aria-expanded={view.expanded}
      title={view.title}
      onClick={event=>view.run(event.currentTarget as HTMLElement)}
    >{view.content}</button>
  }
  // Rows are rendered first and *then* filtered on what actually produced a button.
  // Backend filtering alone is not enough: the mutually exclusive built-ins
  // (Relaunch versus Copy reply / Copy resume, Attach on a backend that takes no
  // files) decide at render time and return null, so a row holding only those
  // would otherwise stand as a full-height empty strip.
  const builtRailRows=scrollingRailRows
    .map(row=>({id:row.id,nodes:row.entries.map(renderRailItem).filter((node):node is VNode=>!!node)}))
    .filter(row=>row.nodes.length)
  // The status readout and the settings gear ride the last surviving row, so they
  // stay put as rows are added and a rail configured down to nothing still has a
  // way back into settings.
  const renderedRailRows=builtRailRows.length?builtRailRows:[{id:'rail-empty',nodes:[]}]

  const ownerNotice=inputOwnerNotice(inputOwnership)
  // No style at all when the cap is off, rather than one carrying `maxWidth:'none'`:
  // an uncapped Claude pane then has exactly the host every other backend has, so
  // "the envelope is disabled" and "this build never had an envelope" are the same
  // code path and cannot diverge. The font declarations exist only to give `ch` a
  // cell to resolve against, so they go with it.
  const claudeHostStyle=widthCap?{
    // `justifySelf:center` opts a grid item out of stretch sizing. Keep the width
    // definite before applying the cap or the host becomes shrink-to-fit, with its
    // 100%-wide xterm child feeding the result back into the next FitAddon pass.
    width:'100%',
    maxWidth:claudeHostMaxWidth(widthCap),
    justifySelf:'center',
    fontFamily:'"Cascadia Mono", Consolas, monospace',
    fontSize:`${baseFont}px`,
    fontWeight:'600',
  }:undefined
  // Two lengths the stylesheet cannot know: how far this pane's grid is currently pushed
  // down (a drag writes it at pointer rate), and how much of its own height it is holding
  // back for the keyboard. Both are inert until the mobile rules that read them apply.
  const surfaceStyle={
    '--peek-offset':`${peekOffset}px`,
    '--terminal-keyboard-reserve':`${keyboardReserved?reservePxRef.current:0}px`,
  } as Record<string,string>
    return <div class={`terminal-surface${peekOffset>0?' keyboard-peek':''}${peekAnimated?' keyboard-peek-animated':''}${keyboardReserved?' keyboard-reserved':''}`} style={surfaceStyle}><div class={`terminal-host${letterboxActive?' letterboxed':''}`} style={claudeHostStyle} ref={host} /><input ref={attachmentInputRef} type="file" hidden multiple aria-label="Choose files to attach" onChange={event=>{const files=Array.from(event.currentTarget.files||[]);event.currentTarget.value='';void attachFilesRef.current(files)}}/><textarea ref={mobileLiveInputRef} class="mobile-terminal-live-input" rows={1} aria-label="Live mobile terminal input" autoCapitalize="off" autoCorrect="off" autoComplete="off" spellcheck={false} inputMode="text" enterkeyhint="enter"/>{mobileDraftOpen&&<MobileTerminalDraft sessionName={sessionDisplayName(session)||session.id} text={mobileDraftText} busy={mobileDraftInserting} error={mobileDraftError} onInput={setMobileDraftText} onInsert={()=>void insertMobileDraft()} onClear={()=>setMobileDraftText('')} onClose={closeMobileDraft}/>}<div class={`terminal-action-rail${mobilePinnedSend?' mobile-pinned-send':''}`} role="toolbar" aria-label="Terminal keys and clipboard actions" onClick={event=>pulseRail(event.currentTarget,event.target)}><div class="terminal-action-rows">{renderedRailRows.map((row,index)=><RailStrip key={row.id} chips={row.nodes} label={renderedRailRows.length>1?`Actions, row ${index+1}`:'Actions'} status={index===renderedRailRows.length-1?(selectionText?`${selectionText.length.toLocaleString()} selected${mobileInput.autoCopySelection?' · auto-copy on':''}`:''):undefined} onConfigure={()=>onConfigureRail?.()}/>)}</div>{mobilePinnedSend&&<button class="terminal-mobile-send" title="Send composed input; the keyboard Enter key inserts a newline" aria-label="Send composed input" onClick={()=>sendKey('\r')}><SendIcon/></button>}</div>{peekToggleVisible(effectiveKeyboardInset,peekOffset>0,offTail,appOffTail)&&<button class={`terminal-peek-top${peekOffset>0?' active':''}`} aria-pressed={peekOffset>0} title={peekOffset>0?"Back to the composer":"Look at the top of the screen, the keyboard is covering it"} aria-label={peekOffset>0?"Back to the composer":"Show the top of the terminal"} onMouseDown={holdSoftKeyboard} onClick={()=>applyPeekRef.current('toggle')}>{peekOffset>0?'↓':'↑'}</button>}{clipboardStatus&&<div class="terminal-clip-toast" role="status">{clipboardStatus}</div>}{(offTail||appOffTail)&&<button class="terminal-jump-latest" title="Scroll to the newest output" aria-label="Jump to latest output" onMouseDown={holdSoftKeyboard} onClick={jumpToLatest}>↓</button>}{fileDropActive&&<div class="terminal-image-drop" role="status">Drop files to attach to {session.backend}</div>}{findOpen && <div class="terminal-find" role="search">
    <input value={findQuery} onInput={event => { setFindQuery(event.currentTarget.value); setFindResult('') }} onKeyDown={event => {
      // Stopped here so the keypress is one pop, on this bar's own level.
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); dismissStack.pop() }
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
    :letterboxActive&&letterboxSettled&&<div class="terminal-input-owner letterbox-notice" role="status"><span>Showing {letterboxSize} · sized elsewhere</span><button title="Size this session to this pane" onClick={()=>{resizeToPaneRef.current();focusTerminalInputRef.current()}}>Resize</button></div>}{/* Three notices share one slot at the top of the pane, so they are ordered by
       how much they explain: who has input beats an unexplained grid, which beats a
       grid the user can widen from Settings. A pane that is both letterboxed and
       capped is showing another device's size, and naming this device's envelope
       would be describing something that is not on screen. */}
  {!ownerNotice&&!letterboxActive&&widthCapNotice>0&&<div class="terminal-input-owner width-cap-notice" role="status"><span>Claude panes stop at {widthCapNotice} columns</span><SettingLink variant="link" target="terminals.claudeWidth" title="Change or remove the Claude width limit">Change…</SettingLink></div>}{connectionState!=='connected'&&<div class={`terminal-connection ${connectionState}`} role="status"><span>{connectionState==='ended'?'session ended':connectionState==='connecting'?'connecting…':'reconnecting…'}</span>{connectionState!=='ended'&&<button class="terminal-connection-retry" title="Reconnect now" onClick={()=>reconnectNowRef.current()}>retry</button>}</div>}{manualPaste&&<div class="manual-terminal-paste" role="dialog" aria-label="Paste into terminal"><span>Clipboard read was blocked. Focus here and use your device’s Paste.</span><textarea ref={manualPasteRef} aria-label="Paste terminal text here" onPaste={event=>{
    const data=event.clipboardData
    const image=data&&clipboardImage(Array.from(data.items))
    if(image){event.preventDefault();void attachFilesRef.current([image]).then(()=>setManualPaste(false));return}
    const text=data?.getData('text/plain')||''
    if(text){event.preventDefault();if(termRef.current)pasteIntoTerminal(termRef.current,session,text);focusTerminalInputRef.current();setManualPaste(false);showClipboardStatus('Pasted')}
  }} onInput={event=>{const text=event.currentTarget.value;if(!text)return;if(termRef.current)pasteIntoTerminal(termRef.current,session,text);event.currentTarget.value='';focusTerminalInputRef.current();setManualPaste(false);showClipboardStatus('Pasted')}}/><button aria-label="Cancel paste" onClick={()=>{setManualPaste(false);focusTerminalInputRef.current()}}>×</button></div>}{preparedClipboard&&<div class="prepared-clipboard" role="status"><span>Clipboard write was blocked. Copy the prepared text once.</span><button onClick={()=>void retryPreparedCopy()}>Copy</button><button aria-label="Dismiss prepared clipboard" onClick={()=>{setPreparedClipboard('');setManualClipboard(false)}}>×</button><textarea ref={manualClipboardRef} class={manualClipboard?'manual':''} readOnly value={preparedClipboard} aria-label="Prepared terminal clipboard text" onFocus={event=>event.currentTarget.select()} /></div>}{dropup?.kind==='clipboard'&&<ClipboardDropup anchor={dropup.anchor} onClose={()=>setDropup(null)} onInsert={text=>injectText(text,false)} onOpenSection={()=>runCommand('clipboard.open')}/>}{dropup?.kind==='skills'&&<SkillsDropup sessionId={session.id} harness={harnessDisplayName(session.backend)} anchor={dropup.anchor} onClose={()=>setDropup(null)} onInsert={text=>injectText(text,false)} onOpenSection={()=>runCommand('drawer.actions.skills')}/>}{dropup?.kind==='prompts'&&<PromptsDropup projectId={session.project_id} backend={session.backend} anchor={dropup.anchor} onClose={()=>setDropup(null)} onInsert={text=>injectText(text,false)} onOpenSection={()=>runCommand('drawer.actions.prompts')} onCreate={()=>runCommand('prompts.new')}/>}{menu && <div ref={el=>fitMenuInViewport(el)} class="terminal-menu" role="menu" style={{ left: clampContextMenuLeft(menu.x, innerWidth), top: Math.min(menu.y, innerHeight - 230) }}>
    <button role="menuitem" disabled={!termRef.current?.hasSelection()} onClick={() => runCommand('terminal.copy')}>Copy</button>
    <button role="menuitem" onClick={() => runCommand('terminal.paste')}>Paste</button>
    <button role="menuitem" onClick={() => { setMenu(null); runCommand('clipboard.open') }}>Clipboard history…</button>
    <button role="menuitem" onClick={() => runCommand('terminal.selectAll')}>Select all</button>
    <button role="menuitem" onClick={() => runCommand('terminal.find')}>Find…</button>
    <button role="menuitem" onClick={() => runCommand('terminal.clear')}>Clear display</button>
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
  // the resume Action rail button must pick up the flip.
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
  a.mobileInput === b.mobileInput &&
  // Without this a width envelope edited in Settings reaches no live pane, and the
  // setting appears to do nothing until every terminal is rebuilt.
  a.claudeMaxColumns === b.claudeMaxColumns &&
  // Without this the memo swallows the change and the pane keeps the old font.
  a.uiScale === b.uiScale,
)
