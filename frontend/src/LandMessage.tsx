import { t } from './localization/index.ts'
import { useState } from 'preact/hooks'
import { api } from './api'
import { landRequestMessage } from './gitLand'

/**
 * The daemon's own explanation of how a request ended, with a way to take it elsewhere.
 *
 * The queue has always composed one bounded message per outcome and addressed it to the
 * session that asked. An **operator's** Land has no such session, so `_solicited_reply`
 * dropped it: the one requester who is standing in front of the queue was the only one
 * who never saw why it stopped. The trail now records the text, and this reads it back.
 *
 * Fetched on open rather than with the queue poll, because a bounded handback carries a
 * tail of gate output and a hundred of those on every five-second poll is a different
 * feature. Shown as well as copied, for the reason the setup prompt already is: a copy
 * whose payload nobody can read before pressing is the wrong shape, and
 * `navigator.clipboard` is absent in an insecure context and refusable everywhere, so a
 * refusal has to say so with the text already on screen.
 */
export function LandMessage({ requestId }: { requestId: string }) {
  const [body, setBody] = useState('')
  const [state, setState] = useState<'idle' | 'loading' | 'empty' | 'error'>('idle')
  const [note, setNote] = useState('')

  const load = async () => {
    if (body || state === 'loading') return
    setState('loading')
    try {
      const raw = await api<unknown>('GET', `/api/land/${encodeURIComponent(requestId)}/events`)
      const message = landRequestMessage(raw)
      setBody(message)
      setState(message ? 'idle' : 'empty')
    } catch { setState('error') }
  }

  const copy = async () => {
    try { await navigator.clipboard.writeText(body); setNote('Copied.') }
    catch { setNote(t("LandMessage.5f864edfd7")) }
  }

  return <details class="git-land-message" onToggle={event => {
    setNote('')
    if (event.currentTarget.open) void load()
  }}>
    <summary>{t("LandMessage.aada2b06bf")}</summary>
    {state === 'loading' && <p class="git-state">{t("LandMessage.bd28fafa32")}</p>}
    {state === 'error' && <p class="git-state error">{t("LandMessage.7a1b30e004")}</p>}
    {state === 'empty' && <p class="git-state">{t("LandMessage.16c6590eb0")}</p>}
    {body && <>
      <div>
        <button onClick={() => void copy()}>{t("LandMessage.372c550f57")}</button>
        {note && <small role="status">{note}</small>}
      </div>
      <pre class="git-land-source">{body}</pre>
    </>}
  </details>
}
