import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import {
  CLIPBOARD_CHANGED_EVENT,
  clearClipboardHistory,
  clipboardEntryText,
  deleteClipboardEntry,
  loadClipboardHistory,
  relativeAge,
  setClipboardEntryPinned,
  sourceLabel,
  withoutClipboardCapture,
  type ClipboardEntry,
  type ClipboardHistory,
} from './clipboardHistory'
import { copyPreparedText } from './terminalClipboard'

// The clipboard-history tab of the utility drawer (`UtilityDrawer.tsx` owns the
// host chrome: tab strip, close, mobile scrim, desktop column).
//
// Inserting is the primary action and it targets whatever pane was last focused,
// which is why the drawer is not a modal layer: the workspace has to stay visible,
// and on desktop usable, behind it.
//
// Rows carry previews only; the full text is fetched per entry on use, so a long
// history never ships megabytes into the picker.

type Props = {
  /** Insert into the last-focused terminal/editor; reports what received it. */
  onInsert: (text: string) => 'terminal' | 'editor' | 'none'
  onDone: () => void
  onOpenSettings: () => void
}

export function ClipboardTab({ onInsert, onDone, onOpenSettings }: Props) {
  const [history, setHistory] = useState<ClipboardHistory | null>(null)
  const [query, setQuery] = useState('')
  const [index, setIndex] = useState(0)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)
  const [manual, setManual] = useState('')
  const search = useRef<HTMLInputElement>(null)
  const manualArea = useRef<HTMLTextAreaElement>(null)

  const load = () => loadClipboardHistory().then(setHistory).catch(cause => setNote(cause instanceof Error ? cause.message : String(cause)))
  useEffect(() => { void load(); search.current?.focus() }, [])
  // One event name covers both a local capture and a change another device made
  // (App re-dispatches the daemon's `clipboard_changed` under the same name).
  useEffect(() => {
    const onChanged = () => void load()
    window.addEventListener(CLIPBOARD_CHANGED_EVENT, onChanged)
    return () => window.removeEventListener(CLIPBOARD_CHANGED_EVENT, onChanged)
  }, [])

  const entries = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    const items = history?.entries || []
    return needle ? items.filter(item => item.preview.toLocaleLowerCase().includes(needle)) : items
  }, [history, query])
  useEffect(() => setIndex(current => Math.min(current, Math.max(0, entries.length - 1))), [entries.length])

  const withText = async (entry: ClipboardEntry, use: (text: string) => void | Promise<void>) => {
    setBusy(true)
    try { await use(await clipboardEntryText(entry.id)) }
    catch (cause) { setNote(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy(false) }
  }

  const insert = (entry: ClipboardEntry) => withText(entry, text => {
    const target = onInsert(text)
    if (target === 'none') { setNote('Focus a terminal or note first, then insert.'); return }
    onDone()
  })

  // Copy is transport out of the history surface, not a new capture. Keep the
  // entry's position and timestamp stable while still updating the OS clipboard.
  const copy = (entry: ClipboardEntry) => withText(entry, async text => {
    const area = manualArea.current
    if (area) area.value = text
    const copied = await withoutClipboardCapture(() => copyPreparedText(text, area))
    if (copied) {
      if (area) area.value = ''
      setManual('')
      setNote('Copied to the system clipboard.')
    } else {
      setManual(text)
      setNote('Clipboard blocked — copy the text below manually.')
    }
  })

  const pin = async (entry: ClipboardEntry) => {
    try { await setClipboardEntryPinned(entry.id, !entry.pinned); await load() }
    catch (cause) { setNote(cause instanceof Error ? cause.message : String(cause)) }
  }
  const remove = async (entry: ClipboardEntry) => {
    try { await deleteClipboardEntry(entry.id); await load() }
    catch (cause) { setNote(cause instanceof Error ? cause.message : String(cause)) }
  }
  const clear = async () => {
    try { const removed = await clearClipboardHistory(); setConfirmClear(false); setNote(`Cleared ${removed} entr${removed === 1 ? 'y' : 'ies'}.`); await load() }
    catch (cause) { setNote(cause instanceof Error ? cause.message : String(cause)) }
  }

  const onSearchKey = (event: KeyboardEvent) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); setIndex(value => Math.min(value + 1, Math.max(0, entries.length - 1))) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setIndex(value => Math.max(0, value - 1)) }
    else if (event.key === 'Enter') { event.preventDefault(); const entry = entries[index]; if (entry) void insert(entry) }
  }

  const retention = history?.retention_hours ? `${history.retention_hours}h` : 'until evicted'
  return <>
      <p class="drawer-status">{history ? `${history.count} kept · ${history.persist ? 'saved to disk' : 'memory only'} · ${retention}` : 'loading…'}</p>
      {history && !history.enabled && <p class="clipboard-disabled">Clipboard history is off. <button class="link" onClick={onOpenSettings}>Turn it on in Settings → Input</button>.</p>}
      <div class="clipboard-search">
        <input ref={search} value={query} onInput={event => setQuery(event.currentTarget.value)} onKeyDown={onSearchKey} placeholder="Filter copied text…" aria-label="Filter clipboard history" />
      </div>
      {/* Plain buttons rather than a listbox: each row carries its own copy/pin/
          delete controls, which a listbox may not contain. */}
      <div class="clipboard-entries" role="group" aria-label="Copied text">
        {entries.map((entry, position) => <div key={entry.id} class={`clipboard-entry ${position === index ? 'active' : ''} ${entry.pinned ? 'pinned' : ''}`}>
          <button class="clipboard-entry-body" disabled={busy} title="Insert into the last focused terminal or note" onMouseEnter={() => setIndex(position)} onClick={() => void insert(entry)}>
            <span>{entry.preview}</span>
            <small>{entry.pinned ? '★ ' : ''}{sourceLabel(entry.source)} · {relativeAge(entry.updated_at)} · {entry.char_count.toLocaleString()} chars{entry.line_count > 1 ? ` · ${entry.line_count} lines` : ''}{entry.device ? ` · ${entry.device}` : ''}</small>
          </button>
          <div class="clipboard-entry-actions">
            <button disabled={busy} title="Copy to the system clipboard" aria-label="Copy to system clipboard" onClick={() => void copy(entry)}>⧉</button>
            <button title={entry.pinned ? 'Unpin (pinned entries survive eviction and clear)' : 'Pin (survives eviction and clear)'} aria-label={entry.pinned ? 'Unpin entry' : 'Pin entry'} onClick={() => void pin(entry)}>{entry.pinned ? '★' : '☆'}</button>
            <button title="Forget this entry" aria-label="Delete entry" onClick={() => void remove(entry)}>×</button>
          </div>
        </div>)}
        {history && !entries.length && <p class="clipboard-empty">{history.entries.length ? 'No entry matches that filter.' : history.enabled ? 'Nothing copied yet. Copies made inside swe-mux land here — the OS clipboard is never read.' : 'History is off, so nothing is being kept.'}</p>}
      </div>
      {/* Mounted even when empty: the legacy `execCommand('copy')` fallback needs a
          live textarea inside the same user gesture, so it cannot wait for a
          re-render after the modern write is refused. */}
      <div class={`clipboard-manual ${manual ? 'shown' : ''}`}><textarea ref={manualArea} readOnly value={manual} data-clipboard-capture="ignore" aria-label="Text to copy manually" /><button onClick={() => setManual('')}>Done</button></div>
      {note && <p class="clipboard-note" aria-live="polite">{note}</p>}
      <footer class="drawer-actions">
        {confirmClear
          ? <><button class="danger" onClick={() => void clear()}>Confirm clear</button><button onClick={() => setConfirmClear(false)}>Cancel</button></>
          : <button class="danger" disabled={!history?.count} onClick={() => setConfirmClear(true)}>Clear unpinned</button>}
        <button onClick={onOpenSettings}>Settings</button>
      </footer>
  </>
}
