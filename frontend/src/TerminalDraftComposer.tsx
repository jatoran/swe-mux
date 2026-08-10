import { useEffect, useLayoutEffect, useRef } from 'preact/hooks'
import { holdSoftKeyboard } from './mobileKeyboard'
import { MOBILE_TERMINAL_DRAFT_MAX_CHARS } from './mobileTerminalDraft'

type Props = {
  sessionName: string
  text: string
  busy: boolean
  error: string
  onInput: (text: string) => void
  onInsert: () => void
  onClear: () => void
  onClose: () => void
}

/** Visible mobile composition buffer. Terminal ownership remains with TerminalPane. */
export function MobileTerminalDraft({
  sessionName, text, busy, error, onInput, onInsert, onClear, onClose,
}: Props) {
  const input = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const element = input.current
      if (!element) return
      element.focus({ preventScroll: true })
      element.setSelectionRange(element.value.length, element.value.length)
    })
    return () => cancelAnimationFrame(frame)
  }, [])
  useLayoutEffect(() => {
    const element = input.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${element.scrollHeight + element.offsetHeight - element.clientHeight}px`
  }, [text])
  const insert = () => { if (!busy && text) onInsert() }
  return <section class="mobile-terminal-draft" aria-label={`Draft message for ${sessionName}`}>
    <header>
      <strong>Draft</strong>
      <span title={sessionName}>{sessionName}</span>
      <button type="button" aria-label="Hide draft composer" title="Hide draft composer; text stays saved" onMouseDown={holdSoftKeyboard} onClick={onClose}>×</button>
    </header>
    <textarea
      ref={input}
      value={text}
      maxLength={MOBILE_TERMINAL_DRAFT_MAX_CHARS}
      disabled={busy}
      aria-label="Persistent terminal draft"
      placeholder="Write here without sending to the terminal..."
      onInput={event => onInput(event.currentTarget.value)}
      onKeyDown={event => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
          event.preventDefault()
          insert()
        }
      }}
    />
    {error && <p class="mobile-terminal-draft-error" role="alert">{error}</p>}
    <footer>
      <span>{text.length.toLocaleString()} · device-local</span>
      <button type="button" disabled={busy || !text} onMouseDown={holdSoftKeyboard} onClick={onClear}>Clear</button>
      <button type="button" class="primary" disabled={busy || !text} title="Insert into the agent composer without submitting" onMouseDown={holdSoftKeyboard} onClick={insert}>{busy ? 'Inserting...' : 'Insert'}</button>
    </footer>
  </section>
}
