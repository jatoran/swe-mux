// Dismissing the soft keyboard when a full-height mobile panel slides in.
//
// On a phone the sidebar and the utility drawer cover the workspace, but the
// on-screen keyboard raised by whatever was focused behind them does not go
// away on its own — it keeps up to half the screen, so the panel that just
// opened is the half the user cannot see. Blurring the focused field is the
// only way to lower it: there is no API for "hide the keyboard".
//
// This is not the terminal's read/select mode (`terminal.keyboardToggle`),
// which is a sticky per-pane mode. Nothing here is sticky — tapping the
// terminal (or any field) after closing the panel raises the keyboard again.
//
// The predicate and the shadow walk are duck-typed rather than written against
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

/** Lower the soft keyboard, if a field is what is holding it up. Focus on anything
 *  else (a button, the body) is left alone: blurring it would cost the tab order
 *  its place for no gain. */
export function dismissSoftKeyboard():void {
  softKeyboardHolder()?.blur()
}
