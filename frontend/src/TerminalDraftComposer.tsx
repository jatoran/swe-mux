import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks'
import { holdSoftKeyboard } from './mobileKeyboard'
import { MOBILE_TERMINAL_DRAFT_MAX_CHARS } from './mobileTerminalDraft'
import { CopyIcon, InsertArrowIcon } from './railIcons'
import { copyPreparedText } from './terminalClipboard'

type Props = {
  sessionName: string
  text: string
  busy: boolean
  error: string
  onInput: (text: string) => void
  onInsert: () => void
  onClear: () => void
}

const COPIED_NOTICE_MS = 1600

/**
 * Visible mobile composition buffer. Terminal ownership remains with TerminalPane.
 *
 * The composer floats over a phone-sized terminal, so every row it holds is a row of the
 * session the user is reading. It therefore carries no title bar: the section is already
 * labelled for assistive tech, the session it belongs to is the pane behind it, and the
 * only way in is the rail's keyboard toggle — which is also the way out, so a close button
 * would have been a second control for a gesture the user has just made.
 */
export function MobileTerminalDraft({
  sessionName, text, busy, error, onInput, onInsert, onClear,
}: Props) {
  const input = useRef<HTMLTextAreaElement>(null)
  const [copyState, setCopyState] = useState<'' | 'done' | 'failed'>('')
  const copyTimer = useRef(0)
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const element = input.current
      if (!element) return
      element.focus({ preventScroll: true })
      element.setSelectionRange(element.value.length, element.value.length)
    })
    return () => cancelAnimationFrame(frame)
  }, [])
  useEffect(() => () => { if (copyTimer.current) clearTimeout(copyTimer.current) }, [])

  /**
   * Grow to the text, up to the CSS cap, without losing the reader's place.
   *
   * Measuring needs a collapsed box, and collapsing one that is already scrolled resets its
   * offset — which on a touch browser reads as the view jumping to the top on every keystroke
   * once the draft outgrows the cap. The offset is restored around the measurement, and when
   * the caret sits at the end (what "typing a long message" means) it is pinned to the bottom
   * instead, so the line being typed stays visible rather than scrolling out from under itself.
   */
  useLayoutEffect(() => {
    const element = input.current
    if (!element) return
    const scrollTop = element.scrollTop
    const caretAtEnd = element.selectionStart === element.selectionEnd
      && element.selectionEnd === element.value.length
    element.style.height = 'auto'
    element.style.height = `${element.scrollHeight + element.offsetHeight - element.clientHeight}px`
    element.scrollTop = caretAtEnd ? element.scrollHeight : scrollTop
  }, [text])

  const insert = () => { if (!busy && text) onInsert() }
  const noteCopy = (state: 'done' | 'failed') => {
    setCopyState(state)
    if (copyTimer.current) clearTimeout(copyTimer.current)
    copyTimer.current = window.setTimeout(() => setCopyState(''), COPIED_NOTICE_MS)
  }
  const copy = async () => {
    if (busy || !text) return
    const element = input.current
    // `copyPreparedText` falls back to selecting the field inside the gesture, which is the only
    // path that works on a mobile browser that has already expired the Clipboard permission.
    // That leaves the whole draft selected, so the caret is put back where the user left it.
    const selectionStart = element?.selectionStart ?? 0
    const selectionEnd = element?.selectionEnd ?? 0
    const scrollTop = element?.scrollTop ?? 0
    const copied = await copyPreparedText(text, element)
    if (element) {
      element.setSelectionRange(selectionStart, selectionEnd)
      element.scrollTop = scrollTop
    }
    noteCopy(copied ? 'done' : 'failed')
  }

  // The two transient states used to be the Insert button's own label; an icon has nowhere to
  // put them, so they land in the counter beside it rather than being dropped.
  const status = busy
    ? 'Inserting...'
    : copyState === 'done'
      ? 'Copied'
      : `${text.length.toLocaleString()} · device-local`
  const notice = error || (copyState === 'failed' ? 'The draft could not be copied on this device.' : '')
  return <section class="mobile-terminal-draft" aria-label={`Draft message for ${sessionName}`}>
    <header aria-hidden="true" />
    <textarea
      ref={input}
      value={text}
      maxLength={MOBILE_TERMINAL_DRAFT_MAX_CHARS}
      disabled={busy}
      aria-label="Persistent terminal draft"
      placeholder="Write here without sending to the terminal..."
      onInput={event => { setCopyState(''); onInput(event.currentTarget.value) }}
      onKeyDown={event => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
          event.preventDefault()
          insert()
        }
      }}
    />
    {notice && <p class="mobile-terminal-draft-error" role="alert">{notice}</p>}
    <footer>
      <span>{status}</span>
      <button type="button" disabled={busy || !text} onMouseDown={holdSoftKeyboard} onClick={onClear}>Clear</button>
      <button type="button" class="icon" disabled={busy || !text} aria-label="Copy draft" title="Copy the whole draft to the clipboard" onMouseDown={holdSoftKeyboard} onClick={() => void copy()}><CopyIcon /></button>
      <button type="button" class="icon primary" disabled={busy || !text} aria-label="Insert" title="Insert into the agent composer without submitting" onMouseDown={holdSoftKeyboard} onClick={insert}><InsertArrowIcon /></button>
    </footer>
  </section>
}
