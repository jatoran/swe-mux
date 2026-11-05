import { locale as uiLocale } from './localization/index.ts'
import { rich, t } from './localization/index.ts'
import { useEffect, useState } from 'preact/hooks'

import {
  CLIPBOARD_CHANGED_EVENT, clipboardEntryText, loadClipboardHistory, relativeAge, sourceLabel,
  type ClipboardHistory,
} from './clipboardHistory'
import { RailDropup } from './RailDropup'

// Clipboard history as the rail sees it: the newest copies, one tap from the
// composer they are going into.
//
// This is the *paste* path on touch, not merely a shortcut to one. Reading the
// system clipboard needs a permission that a plain-HTTP tailnet client does not
// have and that mobile Safari refuses outright, which is why the drawer's
// Clipboard section exists at all (`remote-access.md`). Inserting from mux's own
// ring never touches `navigator.clipboard`, so it works everywhere the pane does.
//
// One action per row, deliberately. The drawer section makes a row expandable
// because a two-line preview cannot always tell two similar copies apart, and
// getting that wrong there costs a mis-insert. Here the answer to "which one is
// it?" is the sticky link at the top: go read them properly. A drop-up that both
// inserted and expanded would rebuild the surface it is a shortcut past.

type Props = {
  anchor: HTMLElement | null
  onClose: () => void
  /** Insert into the pane this rail belongs to. Never submits. */
  onInsert: (text: string) => Promise<void>
  /** Open the Clipboard section of the Actions drawer. */
  onOpenSection: () => void
}

export function ClipboardDropup({ anchor, onClose, onInsert, onOpenSection }: Props) {
  const [history, setHistory] = useState<ClipboardHistory | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState('')

  const load = () => loadClipboardHistory()
    .then(setHistory)
    .catch(cause => setNote(cause instanceof Error ? cause.message : String(cause)))
  useEffect(() => { void load() }, [])
  // A copy made on another device reaches here the same way it reaches the drawer
  // section: App re-dispatches the daemon's `clipboard_changed` under this name.
  useEffect(() => {
    const changed = () => void load()
    window.addEventListener(CLIPBOARD_CHANGED_EVENT, changed)
    return () => window.removeEventListener(CLIPBOARD_CHANGED_EVENT, changed)
  }, [])

  const insert = async (entryId: string) => {
    setBusy(entryId)
    setNote('')
    try {
      await onInsert(await clipboardEntryText(entryId))
      onClose()
    } catch (cause) {
      setNote(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy('')
    }
  }

  const entries = history?.entries || []
  return <RailDropup
    label={t("ClipboardDropup.a96363c972")}
    anchor={anchor}
    onClose={onClose}
    sticky={{
      get label() { return t("ClipboardDropup.fd5353ee11") },
      get title() { return t("ClipboardDropup.9606b9e6d5") },
      run: onOpenSection,
    }}
  >
    {!history && !note && <p class="rail-dropup-empty">{t("ClipboardDropup.ba3bbbe10d")}</p>}
    {history && !history.enabled && <p class="rail-dropup-empty">{t("ClipboardDropup.b7bdc09072")}</p>}
    {history?.enabled && !entries.length && <p class="rail-dropup-empty">{t("ClipboardDropup.c4b608cdce")}</p>}
    {entries.map(entry => <button
      key={entry.id}
      type="button"
      class="rail-dropup-row"
      disabled={!!busy}
      title={t("ClipboardDropup.8c7f5950bd", { value1: entry.char_count.toLocaleString(uiLocale.value), value2: sourceLabel(entry.source) })}
      onClick={() => void insert(entry.id)}
    >
      <span>{busy === entry.id ? t("ClipboardDropup.9d42b19528") : entry.preview}</span>
      <small>{rich("ClipboardDropup.15f0a6bdfb", { value1: (entry.pinned) ? 'yes' : 'no', value2: sourceLabel(entry.source), value3: relativeAge(entry.updated_at), value4: entry.char_count.toLocaleString(uiLocale.value) })}</small>
    </button>)}
    {note && <p class="rail-dropup-note" role="alert">{note}</p>}
  </RailDropup>
}
