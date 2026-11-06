import { isComposingKey } from './inputComposition.ts'
import { catalogRevision, t, locale as uiLocale } from './localization/index.ts'
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
  const catalog=useMemo(()=>completeVoiceReference(commands,configuredCommands||[]), [commands, configuredCommands, uiLocale.value, catalogRevision.value])
  useEffect(() => {
    if (!open) return
    const close = (event: KeyboardEvent) => {
      if (isComposingKey(event)) return
 if (event.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', close, true)
    return () => window.removeEventListener('keydown', close, true)
  }, [open])
  const dialog = open ? createPortal(<div class="voice-command-dialog-backdrop" onPointerDown={event => {
    if (event.target === event.currentTarget) setOpen(false)
  }}>
    <section class="voice-command-dialog" role="dialog" aria-modal="true" aria-label={t("heading.7fca441bba")}>
      <header><strong>{t("heading.7fca441bba")}</strong><button aria-label={t("VoiceCommandsButton.34789a7e73")} onClick={() => setOpen(false)}>×</button></header>
      <div class="voice-command-dialog-groups">
        {catalog.map(section=><section key={section.id}>
          <h3>{section.title} <span>{section.phrases.length+section.commands.length}</span></h3>
          {!!section.phrases.length && <div class="voice-dialog-phrases">{section.phrases.map(phrase=><code key={phrase}>{phrase}</code>)}</div>}
          {!!section.commands.length && <div class="voice-dialog-commands">{section.commands.map(command=><article key={command.id}>
            <strong>{command.label}</strong>
            {!command.available && <small>{command.disabledReason || t("VoiceCommandsButton.61166d13af")}</small>}
            <div>{command.phrases.map(phrase=><code key={phrase}>{phrase}</code>)}</div>
          </article>)}</div>}
        </section>)}
      </div>
    </section>
  </div>, document.body) : null
  return <>
    <button class="voice-commands-open" title={t("VoiceCommandsButton.3f27a29173")} aria-haspopup="dialog" onClick={() => setOpen(true)}>
      {compact ? '?' : t("VoiceCommandsButton.1910855d17")}
    </button>
    {dialog}
  </>
}
