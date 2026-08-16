import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'preact/hooks'
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
import { hasSoftKeyboard } from './deviceSettings'
import { copyPreparedText } from './terminalClipboard'

// The clipboard-history tab of the utility drawer (`UtilityDrawer.tsx` owns the
// host chrome: tab strip, close, mobile scrim, desktop column).
//
// Inserting is the primary action and it targets whatever pane was last focused,
// which is why the drawer is not a modal layer: the workspace has to stay visible,
// and on desktop usable, behind it. Tapping a row is *reading*, not acting: it
// expands the entry so the full text can be read and part-selected, because a
// two-line preview cannot tell two similar copies apart and one-tap-inserts of
// the wrong one were the cost.
//
// Rows carry previews only; the full text is fetched per entry when the row is
// expanded or acted on, so a long history never ships megabytes into the picker.
// Fetched text is cached per entry (an entry's text never changes, since a re-copy
// promotes the existing row rather than rewriting it), which also lets Copy on an
// already-open entry run inside the click gesture, where the legacy `execCommand`
// fallback still works.

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
  const [expanded, setExpanded] = useState('')
  const [texts, setTexts] = useState<Record<string, string>>({})
  const search = useRef<HTMLInputElement>(null)
  const manualArea = useRef<HTMLTextAreaElement>(null)
  const openRow = useRef<HTMLElement | null>(null)

  const load = () => loadClipboardHistory().then(setHistory).catch(cause => setNote(cause instanceof Error ? cause.message : String(cause)))
  useEffect(() => {
    void load()
    // Never on a touch device: autofocusing the filter throws the soft keyboard up
    // over the history the user opened the tab to read, before they have asked to
    // type anything. There, the keyboard arrives by tapping the field.
    if (!hasSoftKeyboard()) search.current?.focus()
  }, [])
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

  // An expanded entry is routinely taller than the viewport, and opening one that
  // sits low in the list otherwise drops the reader into the middle of its body.
  useLayoutEffect(() => {
    if (expanded) openRow.current?.scrollIntoView({ block: 'nearest' })
  }, [expanded])

  const readText = async (entry: ClipboardEntry): Promise<string> => {
    const cached = texts[entry.id]
    if (cached !== undefined) return cached
    const text = await clipboardEntryText(entry.id)
    setTexts(current => ({ ...current, [entry.id]: text }))
    return text
  }

  const withText = async (entry: ClipboardEntry, use: (text: string) => void | Promise<void>) => {
    const cached = texts[entry.id]
    // Cached text keeps the whole action synchronous with the click, which is what
    // the `execCommand` copy fallback needs; only a cold entry pays the await.
    if (cached !== undefined) {
      try { await use(cached) }
      catch (cause) { setNote(cause instanceof Error ? cause.message : String(cause)) }
      return
    }
    setBusy(true)
    try { await use(await readText(entry)) }
    catch (cause) { setNote(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy(false) }
  }

  // One entry open at a time: two sticky action bars on screen at once have no
  // clear owner, and the ring holds far more text than is worth keeping resident.
  const toggle = (entry: ClipboardEntry) => {
    if (expanded === entry.id) { setExpanded(''); return }
    setExpanded(entry.id)
    setNote('')
    if (texts[entry.id] === undefined) {
      void readText(entry).catch(cause => setNote(cause instanceof Error ? cause.message : String(cause)))
    }
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
    try {
      await deleteClipboardEntry(entry.id)
      if (expanded === entry.id) setExpanded('')
      await load()
    } catch (cause) { setNote(cause instanceof Error ? cause.message : String(cause)) }
  }
  const clear = async () => {
    try {
      const removed = await clearClipboardHistory()
      setConfirmClear(false)
      setExpanded('')
      setNote(`Cleared ${removed} entr${removed === 1 ? 'y' : 'ies'}.`)
      await load()
    } catch (cause) { setNote(cause instanceof Error ? cause.message : String(cause)) }
  }

  // The filter keeps its keyboard fast path: Enter still inserts the active row
  // without a trip through the list, and the arrows now also open and close it.
  const onSearchKey = (event: KeyboardEvent) => {
    const entry = entries[index]
    if (event.key === 'ArrowDown') { event.preventDefault(); setIndex(value => Math.min(value + 1, Math.max(0, entries.length - 1))) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setIndex(value => Math.max(0, value - 1)) }
    else if (event.key === 'Enter') { event.preventDefault(); if (entry) void insert(entry) }
    else if (event.key === 'ArrowRight' && entry && expanded !== entry.id) { event.preventDefault(); toggle(entry) }
    else if (event.key === 'ArrowLeft' && expanded) { event.preventDefault(); setExpanded('') }
  }

  // One definition per action, shared by the row's inline bar and the expanded
  // footer so the two surfaces never drift. Insert and Copy are `cb-primary` (kept
  // one tap away everywhere); Pin and Delete are `cb-secondary` — revealed on hover
  // on desktop and folded into the expanded footer on mobile, so a collapsed row is
  // pure preview rather than a persistent button row.
  const actionButton = (entry: ClipboardEntry, kind: 'insert' | 'copy' | 'pin' | 'delete') => {
    if (kind === 'insert') return <button key="insert" class="cb-act cb-primary" disabled={busy} title="Insert into the last focused terminal or note" aria-label="Insert into the last focused terminal or note" onClick={() => void insert(entry)}><span aria-hidden="true">↵</span><b>Insert</b></button>
    if (kind === 'copy') return <button key="copy" class="cb-act cb-primary" disabled={busy} title="Copy to the system clipboard" aria-label="Copy to system clipboard" onClick={() => void copy(entry)}><span aria-hidden="true">⧉</span><b>Copy</b></button>
    if (kind === 'pin') return <button key="pin" class="cb-act cb-secondary" title={entry.pinned ? 'Unpin (pinned entries survive eviction and clear)' : 'Pin (survives eviction and clear)'} aria-label={entry.pinned ? 'Unpin entry' : 'Pin entry'} onClick={() => void pin(entry)}><span aria-hidden="true">{entry.pinned ? '★' : '☆'}</span><b>{entry.pinned ? 'Unpin' : 'Pin'}</b></button>
    return <button key="delete" class="cb-act cb-secondary" title="Forget this entry" aria-label="Delete entry" onClick={() => void remove(entry)}><span aria-hidden="true">×</span><b>Delete</b></button>
  }

  const retention = history?.retention_hours ? `${history.retention_hours}h` : 'until evicted'
  return <>
      <p class="drawer-status">{history ? `${history.count} kept · ${history.persist ? 'saved to disk' : 'memory only'} · ${retention}` : 'loading…'}</p>
      {history && !history.enabled && <p class="clipboard-disabled">Clipboard history is off. <button class="link" onClick={onOpenSettings}>Turn it on in Settings → Input</button>.</p>}
      <div class="clipboard-search">
        <input ref={search} value={query} onInput={event => setQuery(event.currentTarget.value)} onKeyDown={onSearchKey} placeholder="Filter copied text…" aria-label="Filter clipboard history" />
      </div>
      {/* Articles rather than a listbox: each row carries its own action bar and an
          expandable body, neither of which a listbox option may contain. */}
      <div class="clipboard-entries" role="group" aria-label="Copied text">
        {entries.map((entry, position) => {
          const open = expanded === entry.id
          const text = texts[entry.id]
          return <article
            key={entry.id}
            ref={open ? openRow : undefined}
            class={`clipboard-entry${position === index ? ' active' : ''}${entry.pinned ? ' pinned' : ''}${open ? ' expanded' : ''}`}
          >
            {/* Sticky once open, so this entry's own actions stay on screen for the
                whole scroll of a long body instead of being left behind above it. */}
            <header class="clipboard-entry-head">
              <button
                class="clipboard-entry-body"
                aria-expanded={open}
                title={open ? 'Collapse' : 'Expand to read and select the full text'}
                onMouseEnter={() => setIndex(position)}
                onClick={() => toggle(entry)}
              >
                <span class="clipboard-entry-caret" aria-hidden="true">{open ? '⌄' : '›'}</span>
                <span class="clipboard-entry-preview">{entry.preview}</span>
                <small>{entry.pinned ? '★ ' : ''}{sourceLabel(entry.source)} · {relativeAge(entry.updated_at)} · {entry.char_count.toLocaleString()} chars{entry.line_count > 1 ? ` · ${entry.line_count} lines` : ''}{entry.device ? ` · ${entry.device}` : ''}</small>
              </button>
              <div class="clipboard-entry-actions">
                {actionButton(entry, 'insert')}
                {actionButton(entry, 'copy')}
                {actionButton(entry, 'pin')}
                {actionButton(entry, 'delete')}
              </div>
            </header>
            {open && <>
              {/* Selectable, and opted out of capture: a part-selection copied back
                  out of the history surface is transport, not a new copy, and
                  capturing it would reorder the list under the reader. */}
              <pre class="clipboard-entry-text" data-clipboard-capture="ignore">{text === undefined ? 'Loading…' : text}</pre>
              {/* Full toolbar for the open entry: Pin/Delete live here on mobile
                  (where the collapsed row hides them), with Collapse to exit. */}
              <footer class="clipboard-entry-foot">{actionButton(entry, 'pin')}{actionButton(entry, 'delete')}<button class="cb-collapse" onClick={() => setExpanded('')}>Collapse</button></footer>
            </>}
          </article>
        })}
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
