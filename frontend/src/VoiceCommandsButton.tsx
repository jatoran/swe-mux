import { createPortal } from 'preact/compat'
import { useEffect, useState } from 'preact/hooks'
import { VOICE_HELP_COMMANDS } from './voiceQueries'
import type { VoiceHelpCategory } from './voiceQueries'

const categoryLabel = (category: VoiceHelpCategory): string => category[0].toUpperCase() + category.slice(1)

/** Shared command catalog for the Talk panel and each read-aloud strip. */
export function VoiceCommandsButton({ compact = false }: { compact?: boolean }) {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (!open) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', close, true)
    return () => window.removeEventListener('keydown', close, true)
  }, [open])
  const dialog = open ? createPortal(<div class="voice-command-dialog-backdrop" onPointerDown={event => {
    if (event.target === event.currentTarget) setOpen(false)
  }}>
    <section class="voice-command-dialog" role="dialog" aria-modal="true" aria-label="Voice commands">
      <header><strong>Voice commands</strong><button aria-label="Close voice commands" onClick={() => setOpen(false)}>×</button></header>
      <div class="voice-command-dialog-groups">
        {(Object.keys(VOICE_HELP_COMMANDS) as VoiceHelpCategory[]).map(category => <section key={category}>
          <h3>{categoryLabel(category)}</h3>
          <div>{VOICE_HELP_COMMANDS[category].map(command => <code key={command}>{command}</code>)}</div>
        </section>)}
      </div>
    </section>
  </div>, document.body) : null
  return <>
    <button class="voice-commands-open" title="Show voice commands" aria-haspopup="dialog" onClick={() => setOpen(true)}>
      {compact ? '?' : '? Commands'}
    </button>
    {dialog}
  </>
}
