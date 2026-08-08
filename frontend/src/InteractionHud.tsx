import { useEffect, useRef, useState } from 'preact/hooks'
import {
  CLIPBOARD_COPIED_EVENT,
  type ClipboardCopiedDetail,
} from './clipboardHistory'

export const INTERACTION_HUD_EVENT = 'mux:interaction-hud'
const INTERACTION_HUD_DURATION_MS = 1400

type InteractionHudDetail = {
  text: string
}

/** Request transient feedback without updating the workspace composition root. */
export function showInteractionHud(text: string): void {
  if (!text || typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent<InteractionHudDetail>(INTERACTION_HUD_EVENT, {
    detail: { text },
  }))
}

/**
 * Isolated feedback owner.
 *
 * Copy and cut can fire while a custom editor or terminal selection is active.
 * Keeping this state below App prevents the acknowledgement from re-rendering
 * those surfaces and disturbing the browser's selection or edit transaction.
 */
export function InteractionHud() {
  const [text, setText] = useState('')
  const timer = useRef<number | null>(null)

  useEffect(() => {
    const show = (message: string) => {
      if (!message) return
      setText(message)
      if (timer.current !== null) window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => {
        setText('')
        timer.current = null
      }, INTERACTION_HUD_DURATION_MS)
    }
    const onInteraction = (event: Event) => {
      show((event as CustomEvent<InteractionHudDetail>).detail?.text || '')
    }
    const onClipboardCopied = (event: Event) => {
      const detail = (event as CustomEvent<ClipboardCopiedDetail>).detail
      show(detail?.action === 'cut' ? 'Cut to clipboard' : 'Copied to clipboard')
    }

    window.addEventListener(INTERACTION_HUD_EVENT, onInteraction)
    window.addEventListener(CLIPBOARD_COPIED_EVENT, onClipboardCopied)
    return () => {
      window.removeEventListener(INTERACTION_HUD_EVENT, onInteraction)
      window.removeEventListener(CLIPBOARD_COPIED_EVENT, onClipboardCopied)
      if (timer.current !== null) window.clearTimeout(timer.current)
    }
  }, [])

  if (!text) return null
  return <div class="interaction-hud" role="status" aria-live="polite" aria-atomic="true">{text}</div>
}
