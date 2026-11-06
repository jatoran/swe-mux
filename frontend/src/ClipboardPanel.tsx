import { isComposingKey } from './inputComposition.ts'
import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'preact/hooks'
import { CompactGrantFlag } from './GrantGate'
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

// Clipboard history, drawn as a section of the Actions tab (`ActionsTab.tsx` owns the
// section chrome; `UtilityDrawer.tsx` owns the host chrome above that: tab strip, close,
// mobile scrim, desktop column). It was its own drawer tab until the consolidation, and
// moved because every surface on Actions ends in text reaching the focused agent — the
// same verb and, here, literally the same `onInsert`/`onDone` contract it always had.
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
  /**
   * Focus the filter, once, per new non-zero token.
   *
   * As a tab this autofocused on mount, which was right: opening the tab *was* the
   * request. As a section it is on screen whenever Actions is, so focusing on mount would
   * steal the caret from the terminal every time the drawer opened — and on a phone would
   * throw the soft keyboard up over the drawer content above it. So the focus now follows a
   * deliberate arrival (the `drawer.actions.clipboard` command, or the Clipboard Action
   * button) rather than the render.
   */
  autoFocusToken?: number
}

export function ClipboardTab({ onInsert, onDone, onOpenSettings, autoFocusToken = 0 }: Props) {
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
  useEffect(() => { void load() }, [])
  useEffect(() => {
    if (!autoFocusToken) return
    // Never on a touch device: autofocusing the filter throws the soft keyboard up
    // over the history the user came here to read, before they have asked to
    // type anything. There, the keyboard arrives by tapping the field.
    if (!hasSoftKeyboard()) search.current?.focus()
  }, [autoFocusToken])
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
  }, [history, query, uiLocale.value, catalogRevision.value])
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
    if (target === 'none') { setNote(t("ClipboardPanel.471c87714d")); return }
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
      setNote(t("ClipboardPanel.6aa8cf268a"))
    } else {
      setManual(text)
      setNote(t("ClipboardPanel.16c66b35cb"))
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
      setNote(t("ClipboardPanel.020f0c8218", { value1: removed, value2: (removed === 1) ? 'yes' : 'no' }))
      await load()
    } catch (cause) { setNote(cause instanceof Error ? cause.message : String(cause)) }
  }

  // The filter keeps its keyboard fast path: Enter still inserts the active row
  // without a trip through the list, and the arrows now also open and close it.
  const onSearchKey = (event: KeyboardEvent) => {
      if (isComposingKey(event)) return

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
    if (kind === 'insert') return <button key="insert" class="cb-act cb-primary" disabled={busy} title={t("ClipboardPanel.a9c1393c98")} aria-label={t("ClipboardPanel.a9c1393c98")} onClick={() => void insert(entry)}><span aria-hidden="true">↵</span><b>{t("ClipboardPanel.8aa318eb7e")}</b></button>
    if (kind === 'copy') return <button key="copy" class="cb-act cb-primary" disabled={busy} title={t("ClipboardPanel.255f4bb052")} aria-label={t("ClipboardPanel.f1cb6f527d")} onClick={() => void copy(entry)}><span aria-hidden="true">⧉</span><b>{t("ClipboardPanel.e21f935f11")}</b></button>
    if (kind === 'pin') return <button key="pin" class="cb-act cb-secondary" title={entry.pinned ? t("ClipboardPanel.e97957111b") : t("ClipboardPanel.5b74c246ce")} aria-label={entry.pinned ? t("ClipboardPanel.c2087a369b") : t("ClipboardPanel.90e66f95f2")} onClick={() => void pin(entry)}><span aria-hidden="true">{entry.pinned ? '★' : '☆'}</span><b>{entry.pinned ? t("ClipboardPanel.ee3c716130") : t("ClipboardPanel.ff1cee7441")}</b></button>
    return <button key="delete" class="cb-act cb-secondary" title={t("ClipboardPanel.706ff7937f")} aria-label={t("ClipboardPanel.d2968b26e9")} onClick={() => void remove(entry)}><span aria-hidden="true">×</span><b>{t("ClipboardPanel.e2d0a54968")}</b></button>
  }

  const retention = history?.retention_hours ? `${history.retention_hours}h` : t("ClipboardPanel.0a98620e46")
  return <>
      <p class="drawer-status">{history ? t("ClipboardPanel.dcd5c0746d", { count: history.count, value2: (history.persist) ? 'yes' : 'no', retention: retention }) : t("ClipboardPanel.fbc6d752fe")}</p>
      {history && !history.enabled && <CompactGrantFlag id="clipboard.history"
        heading={t("ClipboardPanel.100e5a92c6")}
        consequence={t("ClipboardPanel.22b24e76eb")}
        onGranted={load}/>}
      <div class="clipboard-search">
        <input ref={search} value={query} onInput={event => setQuery(event.currentTarget.value)} onKeyDown={onSearchKey} placeholder={t("ClipboardPanel.72c58edf55")} aria-label={t("ClipboardPanel.d6f1aa0d2b")} />
      </div>
      {/* Articles rather than a listbox: each row carries its own action bar and an
          expandable body, neither of which a listbox option may contain. */}
      <div class="clipboard-entries" role="group" aria-label={t("ClipboardPanel.026eaa7f97")}>
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
                title={open ? t("ClipboardPanel.be6eb1fc3b") : t("ClipboardPanel.6b1d44dd60")}
                onMouseEnter={() => setIndex(position)}
                onClick={() => toggle(entry)}
              >
                <span class="clipboard-entry-caret" aria-hidden="true">{open ? '⌄' : '›'}</span>
                <span class="clipboard-entry-preview">{entry.preview}</span>
                <small>{rich("ClipboardPanel.70a85fe1b1", { value1: (entry.pinned) ? 'yes' : 'no', value2: sourceLabel(entry.source), value3: relativeAge(entry.updated_at), value4: entry.char_count.toLocaleString(uiLocale.value), value5: entry.line_count > 1 ? t("ClipboardPanel.6083078b3f", { line_count: entry.line_count }) : '', value6: entry.device ? t("ClipboardPanel.9a059b94ee", { device: entry.device }) : '' })}</small>
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
              <pre class="clipboard-entry-text" data-clipboard-capture="ignore">{text === undefined ? t("ClipboardPanel.ba3bbbe10d") : text}</pre>
              {/* Full toolbar for the open entry: Pin/Delete live here on mobile
                  (where the collapsed row hides them), with Collapse to exit. */}
              <footer class="clipboard-entry-foot">{actionButton(entry, 'pin')}{actionButton(entry, 'delete')}<button class="cb-collapse" onClick={() => setExpanded('')}>{t("ClipboardPanel.be6eb1fc3b")}</button></footer>
            </>}
          </article>
        })}
        {history && !entries.length && <p class="clipboard-empty">{history.entries.length ? t("ClipboardPanel.e634130c05") : history.enabled ? t("ClipboardPanel.df707bdfb5") : t("ClipboardPanel.7668b9bfb6")}</p>}
      </div>
      {/* Mounted even when empty: the legacy `execCommand('copy')` fallback needs a
          live textarea inside the same user gesture, so it cannot wait for a
          re-render after the modern write is refused. */}
      <div class={`clipboard-manual ${manual ? 'shown' : ''}`}><textarea ref={manualArea} readOnly value={manual} data-clipboard-capture="ignore" aria-label={t("ClipboardPanel.8f9fefb9ba")} /><button onClick={() => setManual('')}>{t("ClipboardPanel.11a6767d56")}</button></div>
      {note && <p class="clipboard-note" aria-live="polite">{note}</p>}
      <footer class="drawer-actions">
        {confirmClear ? <><button class="danger" onClick={() => void clear()}>{t("ClipboardPanel.6271c0fe59")}</button><button onClick={() => setConfirmClear(false)}>{t("ClipboardPanel.19766ed6cc")}</button></> : <button class="danger" disabled={!history?.count} onClick={() => setConfirmClear(true)}>{t("ClipboardPanel.f0bc09994b")}</button>}
        <button onClick={onOpenSettings}>{t("ClipboardPanel.74a883a037")}</button>
      </footer>
  </>
}
