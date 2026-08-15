// Deciding what the soft keyboard does, on the one platform that will not say.
//
// Android exposes no "show/hide the keyboard" API. Its visibility is a function
// of which element has focus: a field that accepts text raises it, and focus
// leaving that field lowers it. Every operation here is therefore expressed as a
// focus move, and the two directions have different callers:
//
//   - Lowering it. On a phone the sidebar and the utility drawer cover the
//     workspace, but the keyboard raised by whatever was focused behind them does
//     not go away on its own — it keeps up to half the screen, so the panel that
//     just opened is the half the user cannot see. `dismissSoftKeyboard` blurs
//     the field holding it up.
//   - Leaving it alone. A control that is not a text field still takes focus when
//     it is pressed, which lowers the keyboard as a side effect of using it — an
//     overlay button (jump-to-latest), or the platform's own handling of a
//     long-press over non-editable content. `holdSoftKeyboard` prevents the focus
//     move before it happens; `restoreSoftKeyboard` puts it back when a gesture
//     already took it. Between them a gesture that was not typing leaves the
//     keyboard exactly as open or closed as it found it.
//
// None of this is the terminal's read/select mode (`terminal.keyboardToggle`),
// which is a sticky per-pane mode. Nothing here is sticky — tapping the terminal
// (or any field) raises the keyboard again.
//
// The predicates and the shadow walk are duck-typed rather than written against
// `HTMLInputElement`/`ShadowRoot` so they can be unit-tested without a DOM.

export type FocusedField = {
  tagName?: string
  type?: string|null
  inputMode?: string|null
  isContentEditable?: boolean
  readOnly?: boolean
  /** Open shadow root, when this is a custom element hosting one. */
  shadowRoot?: FocusScope|null
}

/** Anything that reports its own focused node: `document`, or an open shadow root. */
export type FocusScope = { activeElement?: FocusedField|null }

// Input types that are buttons, pickers, or otherwise never raise a keyboard.
const KEYBOARDLESS_TYPES = new Set([
  'button','checkbox','color','file','hidden','image','radio','range','reset','submit',
])

// Shadow nesting is a handful deep in practice; the cap only exists so a cyclic or
// malformed tree cannot spin this walk forever.
const MAX_SHADOW_DEPTH = 32

/** Would focusing this element be what is holding the soft keyboard up? */
export function raisesSoftKeyboard(element:FocusedField|null|undefined):boolean {
  if(!element)return false
  // `inputMode="none"` is the explicit "I handle my own input" opt-out; honour it
  // wherever it appears, contenteditable included.
  if(element.inputMode==='none')return false
  if(element.isContentEditable)return true
  const tag=(element.tagName||'').toLowerCase()
  if(tag==='textarea')return !element.readOnly
  if(tag!=='input')return false
  // A readonly field is focusable and selectable but never opens a keyboard.
  if(element.readOnly)return false
  return !KEYBOARDLESS_TYPES.has((element.type||'text').toLowerCase())
}

/** Input mode for the terminal IME bridge at a focus boundary.
 *
 * A coarse-pointer device must opt into the virtual keyboard only for typing intent.
 * Keeping `none` while a rail action restores focus lets physical-keyboard input keep
 * working without turning that synthetic action into a request for the soft keyboard.
 */
export function softKeyboardInputMode(
  usesSoftKeyboard:boolean,
  keyboardInset:number,
  typingIntent:boolean,
):'text'|'none' {
  return !usesSoftKeyboard||keyboardInset>0||typingIntent?'text':'none'
}

/**
 * The element the browser really has focus on, descending through open shadow roots.
 *
 * `document.activeElement` retargets to the shadow *host*, so a component that keeps
 * its input inside a shadow root reads from outside as a plain custom element and
 * every predicate here would say "no keyboard". The Continuity markdown editor behind
 * every note and `.md` file is exactly that: a `<textarea>` inside
 * `attachShadow({mode:'open', delegatesFocus:true})`, which is why opening a mobile
 * panel over a note used to leave the keyboard up.
 *
 * A closed shadow root exposes no `activeElement`, so the walk stops at the host —
 * the same answer `document.activeElement` gives on its own, and the best available.
 */
export function deepActiveElement(scope:FocusScope):FocusedField|null {
  let node=scope.activeElement??null
  for(let depth=0;node&&depth<MAX_SHADOW_DEPTH;depth++){
    const inner=node.shadowRoot?.activeElement
    if(!inner||inner===node)break
    node=inner
  }
  return node
}

/** The element currently holding the soft keyboard up, or null if nothing is. */
export function softKeyboardHolder():HTMLElement|null {
  const active=deepActiveElement(document)
  return active instanceof HTMLElement&&raisesSoftKeyboard(active)?active:null
}

/**
 * How many dismissals have been asked for. Not a timestamp: the only question a caller
 * has is "did someone deliberately lower the keyboard since I started", and a counter
 * answers it without any clock or tolerance window.
 */
let dismissals = 0

/** The dismissal count, to compare against later. See `restoreSoftKeyboard`. */
export function softKeyboardDismissals():number {
  return dismissals
}

/** Lower the soft keyboard, if a field is what is holding it up. Focus on anything
 *  else (a button, the body) is left alone: blurring it would cost the tab order
 *  its place for no gain. Counted even when nothing was focused: the *intent* is what
 *  a concurrent gesture has to respect, and whether a field happened to hold the
 *  keyboard at that instant is a race, not a decision. */
export function dismissSoftKeyboard():void {
  dismissals += 1
  softKeyboardHolder()?.blur()
}

/**
 * The height of the layout viewport the soft keyboard is covering.
 *
 * Meaningful only because the viewport meta is `interactive-widget=resizes-visual`: the
 * layout viewport keeps its full height when the keyboard opens, and the visual viewport
 * shrinks to what is still on screen, so the difference is the keyboard.
 *
 * Thresholded because that difference is not always a keyboard. Collapsing browser chrome
 * moves it by ~50-60 px, and pinch-zoom and rounding move it by a few, while no soft
 * keyboard is anywhere near that small. Treating those as a keyboard would slide the
 * workspace up by a sliver every time the address bar hid.
 */
export const SOFT_KEYBOARD_MIN_INSET_PX = 120

export function softKeyboardInset(layoutHeight:number, visualHeight:number):number {
  if(!Number.isFinite(layoutHeight)||!Number.isFinite(visualHeight))return 0
  const inset = Math.round(layoutHeight - visualHeight)
  return inset >= SOFT_KEYBOARD_MIN_INSET_PX ? inset : 0
}

/** Published whenever the keyboard's inset changes, carrying the new inset in pixels. */
export const SOFT_KEYBOARD_EVENT = 'mux:soft-keyboard'

/** Where this device's keyboard measured last, so a pane can reserve its height before it opens. */
const KEYBOARD_INSET_KEY = 'mux.softKeyboardInset'

export function rememberSoftKeyboardInset(inset:number):void {
  if(!Number.isFinite(inset)||inset<=0)return
  try{ localStorage.setItem(KEYBOARD_INSET_KEY,String(Math.round(inset))) }catch{ /* private mode */ }
}

/** The last keyboard inset seen on this device, or 0 if one has never been measured. */
export function lastSoftKeyboardInset():number {
  try{
    const stored=Number(localStorage.getItem(KEYBOARD_INSET_KEY))
    return Number.isFinite(stored)&&stored>0?stored:0
  }catch{ return 0 }
}

/**
 * What can move a terminal between showing the composer and showing the top of its grid.
 *
 * `output` is in this list precisely so it can do nothing. While the keyboard is up the
 * pane shows a slice of a grid taller than the space available, and peeking at the top is
 * how the rest is reached — so the one moment a reader most wants to stay put is while a
 * response is streaming into it. Snapping back on writes would make the toggle useless
 * exactly when it is needed, and it is the easy thing to get backwards, so it is named
 * rather than left implicit.
 *
 * `hiddenOutput` is the one exception, and it is deliberately not the same event. It means
 * the *hidden* half of the grid changed while the reader was parked at the composer with
 * nothing to look at: a first message and its reply on a fresh session land there, and a
 * pane that holds still for that is showing blank rows while the answer paints off-screen.
 * Output the reader can already see never raises it.
 */
export type PeekTrigger = 'toggle'|'input'|'keyboardClosed'|'output'|'hiddenOutput'

/**
 * Whether output landing in the hidden half is worth moving the pane for.
 *
 * The trigger above is written for one situation: a reader parked at the composer *with
 * nothing to look at*, whose first message and its reply both land off-screen. The
 * condition was never written down, only the intent, so it fired for the whole life of
 * every session — and an agent CLI repaints its entire screen constantly, so "the hidden
 * rows changed" is true on nearly every frame. What the reader saw was a pane that
 * jumped to the top by itself, most obviously right after they sent a message, which is
 * exactly when they had stopped typing long enough for the input grace to lapse.
 *
 * So ask the question the intent implies: is there anything on the part of the grid they
 * can already see? If there is, they are reading it, and output arriving above is not
 * worth yanking the view for — they can drag up whenever they want it. Only a reader
 * looking at blank rows has nothing to lose by being moved.
 */
export function hiddenOutputDeservesPeek(input:{
  /** The hidden rows differ from the last reading at this same inset. */
  hiddenChanged:boolean
  /** The hidden region has something on it, rather than being blank too. */
  hiddenHasText:boolean
  /** Rows with anything on them in the part of the grid the reader can see. */
  visiblePainted:number
  /** Where the pane is parked; anything but the composer means they are already reading. */
  peekOffset:number
  /** Milliseconds since the reader last typed, or null if they never have. */
  sinceInputMs:number|null
  /** How recently counts as "still typing". */
  inputGraceMs:number
}):boolean {
  const { hiddenChanged, hiddenHasText, visiblePainted, peekOffset, sinceInputMs, inputGraceMs } = input
  if(!hiddenChanged||!hiddenHasText)return false
  // Already at the top, or typing rather than waiting: the pane moving is an interruption.
  if(peekOffset>0)return false
  if(sinceInputMs!==null&&sinceInputMs<inputGraceMs)return false
  return visiblePainted<=VISIBLE_PAINTED_ROWS_WORTH_READING
}

/**
 * Painted rows in the visible half that still count as "nothing to look at".
 *
 * Not zero. A fresh agent pane parks a prompt marker, a border, or a one-line hint at the
 * bottom of the screen and nothing else, and a reader staring at that has as little to
 * read as one staring at a blank grid. Past a couple of rows there is real content on
 * screen and the pane should hold still.
 */
export const VISIBLE_PAINTED_ROWS_WORTH_READING = 2

/**
 * How far a terminal's grid is pushed back down after `trigger`, in pixels.
 *
 * Zero is the composer; `inset` is the top of the grid; everything between is a reader
 * mid-drag. Typing returns to the composer because that is where the caret is and a reader
 * who types has stopped reading. Losing the keyboard ends it outright: the whole grid fits
 * again, so there is no slice left to move.
 */
export function nextPeekOffset(offset:number, trigger:PeekTrigger, inset:number):number {
  if(trigger==='toggle')return offset>0?0:Math.max(0,inset)
  if(trigger==='hiddenOutput')return Math.max(0,inset)
  if(trigger==='output')return clampPeekOffset(offset,inset)
  return 0
}

/**
 * A drag's new offset, held inside the travel the keyboard actually covers.
 *
 * The pane is a window on a grid taller than the space left over, and this is where the
 * window sits. Clamping rather than rubber-banding because the two ends are real positions
 * a reader parks at — the composer and the top of the grid — and an end that springs back
 * cannot be parked at.
 */
export function clampPeekOffset(offset:number, inset:number):number {
  if(!Number.isFinite(offset)||!Number.isFinite(inset)||inset<=0)return 0
  return Math.min(Math.max(offset,0),inset)
}

/**
 * Whether the peek toggle is offered at all.
 *
 * Not merely "the keyboard is up": at the composer of a conversation with any
 * length the button is clutter over content, and everything hidden by the
 * slide is reachable by scrolling anyway. It appears when the reader is
 * actually reading — scrolled off the tail on either axis, including the
 * app-held viewport whose forwarded drags are only an estimate — which also
 * covers the fresh-session case the toggle exists for: the first swipe up
 * toward a reply trapped under the keyboard is itself the off-tail signal
 * that summons the control. While a peek is active the toggle always shows,
 * because it is the way back down and must not vanish mid-peek.
 */
export function peekToggleVisible(
  keyboardInset:number,
  peeking:boolean,
  offTail:boolean,
  appOffTail:boolean,
):boolean {
  if(keyboardInset<=0)return false
  return peeking||offTail||appOffTail
}

/**
 * Whether a gesture cost the keyboard that was up when it started.
 *
 * Both halves are load-bearing, and the second is what keeps this from being a
 * blunt "focus moved" check. Focus landing on *another* text field is a move the
 * gesture made on purpose and the keyboard never went down, so there is nothing to
 * repair and restoring would yank the user back out of the field they just reached.
 * Only focus that now sits somewhere raising no keyboard is a loss.
 *
 * Split from the DOM so the decision is testable without one.
 */
export function softKeyboardLost(
  holder:FocusedField|null|undefined,
  active:FocusedField|null|undefined,
):boolean {
  return !!holder&&active!==holder&&!raisesSoftKeyboard(active)
}

/**
 * Keep the soft keyboard up while a control that is not a text field is pressed.
 *
 * Bind to `mousedown`, whose default action is the focus move: cancel it and focus
 * never leaves the field, so Android never lowers the keyboard and the control still
 * activates (`click` is unaffected by a cancelled `mousedown`). `pointerdown` would
 * be the tidier event and is not usable — cancelling it suppresses the compatibility
 * mouse events, which is how a phone delivers the press at all.
 *
 * A no-op when no field is holding the keyboard up, so pressing the control with the
 * keyboard already down behaves exactly as it did before, focus ring included.
 */
export function holdSoftKeyboard(event:{preventDefault:()=>void}):void {
  if(softKeyboardHolder())event.preventDefault()
}

/**
 * Put the keyboard back on the field a non-typing gesture took it from.
 *
 * The backstop for the focus moves `holdSoftKeyboard` cannot reach, because they are
 * the platform's rather than a control's: Android lowers the keyboard when a touch
 * resolves against non-editable content, which is what every drag and long-press over
 * a terminal is. Cheaper to re-raise than to fight, and it costs nothing in the common
 * case — a gesture that never moved focus restores nothing.
 *
 * `preventScroll` because the field is the terminal's off-screen IME bridge: scrolling
 * it into view would drag the workspace sideways.
 */
export function restoreSoftKeyboard(
  holder:HTMLElement|null|undefined,
  dismissalsAtGestureStart:number,
):boolean {
  // Somebody lowered the keyboard on purpose while this gesture was running, and that
  // outranks putting it back. The case is not hypothetical: the mobile panels dismiss on
  // open, and the swipe that opens the left sidebar crosses the terminal, so the pane saw
  // a gesture whose focus had "gone missing" and handed the keyboard straight back — the
  // sidebar slid in over a keyboard it had just closed. Opening the same panel with a
  // swipe that starts on the top bar never reached this code, which is exactly why it
  // behaved and the swipe across the session did not.
  if(softKeyboardDismissals()!==dismissalsAtGestureStart)return false
  if(!holder||!softKeyboardLost(holder,deepActiveElement(document)))return false
  holder.focus({preventScroll:true})
  return true
}
