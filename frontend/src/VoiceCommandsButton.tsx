import { createPortal } from 'preact/compat'
import { useEffect, useMemo, useState } from 'preact/hooks'
import type { Command } from './commands'
import { completeVoiceReference, type ConfiguredVoiceCommand } from './voiceCommandReference'

/** Shared command catalog for the Talk panel and each read-aloud strip. */
export function VoiceCommandsButton({
  commands,
  configuredCommands,
  compact=false,
}:{
  commands:Command[]
  configuredCommands?:ConfiguredVoiceCommand[]
  compact?:boolean
}) {
  const [open, setOpen] = useState(false)
  const catalog=useMemo(()=>completeVoiceReference(commands,configuredCommands||[]),[commands,configuredCommands])
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
        {catalog.map(section=><section key={section.id}>
          <h3>{section.title} <span>{section.phrases.length+section.commands.length}</span></h3>
          {!!section.phrases.length&&<div class="voice-dialog-phrases">{section.phrases.map(phrase=><code key={phrase}>{phrase}</code>)}</div>}
          {!!section.commands.length&&<div class="voice-dialog-commands">{section.commands.map(command=><article key={command.id}>
            <strong>{command.label}</strong>
            {!command.available&&<small>{command.disabledReason||'Unavailable in the current workspace state'}</small>}
            <div>{command.phrases.map(phrase=><code key={phrase}>{phrase}</code>)}</div>
          </article>)}</div>}
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
