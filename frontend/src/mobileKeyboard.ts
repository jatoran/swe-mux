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
// The predicate is duck-typed rather than written against `HTMLInputElement`
// so it can be unit-tested without a DOM.

export type FocusedField = {
  tagName?: string
  type?: string|null
  inputMode?: string|null
  isContentEditable?: boolean
  readOnly?: boolean
}

// Input types that are buttons, pickers, or otherwise never raise a keyboard.
const KEYBOARDLESS_TYPES = new Set([
  'button','checkbox','color','file','hidden','image','radio','range','reset','submit',
])

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

/** Lower the soft keyboard, if a field is what is holding it up. Focus on anything
 *  else (a button, the body) is left alone: blurring it would cost the tab order
 *  its place for no gain. */
export function dismissSoftKeyboard():void {
  const active=document.activeElement
  if(active instanceof HTMLElement&&raisesSoftKeyboard(active))active.blur()
}
