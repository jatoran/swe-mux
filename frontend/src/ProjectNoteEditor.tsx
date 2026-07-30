import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import type {
  ContinuityChangeDetail,
  ContinuityEditorElement,
  ContinuityRequestDetail,
  HistoryState,
  RailAction,
  ScrollState,
} from '@continuity-editor/editor'
import { ContinuityEditor } from '@continuity-editor/editor/react'
import { noteQueueKey, noteSaveQueue } from './noteSaveQueue'
import { forgetEditorFocus, noteEditorFocus } from './insertTarget'
import { captureCopy } from './clipboardHistory'
import {
  currentNoteEditorSettings,
  NOTE_EDITOR_EVENT,
  type NoteEditorSettings,
} from './noteEditorSettings'

// Tab switches unmount the whole resource view (only the active stack child renders), so the
// editor instance is destroyed and a fresh one cannot know its previous viewport. Keep the
// last-seen scroll per resource outside the component (like fileDrafts/browserStates) and
// re-apply it when the same resource mounts again.
const scrollStates = new Map<string, ScrollState>()

/**
 * Undo history across that same unmount. The engine is destroyed with the element, and its
 * history used to go with it: you could undo while editing a note, but not after leaving it
 * and coming back. Continuity 0.2.17 makes the history portable, so it is stashed here
 * beside the scroll state and re-imported on the way back in.
 *
 * Bounded on both axes, because a history is far larger than a `ScrollState` and a long
 * session touches many notes: `maxGroups` caps one note's stack, and the map keeps only the
 * most recently stashed notes.
 */
const HISTORY_GROUP_LIMIT = 250
const HISTORY_NOTE_LIMIT = 12
const historyStates = new Map<string, HistoryState>()

function stashHistory(key: string, element: ContinuityEditorElement): void {
  let history: HistoryState
  try {
    history = element.exportHistory({ maxGroups: HISTORY_GROUP_LIMIT })
  } catch {
    return
  }
  // Delete before setting so re-insertion moves this key to the newest end of the
  // iteration order, which is what makes the eviction below least-recently-stashed.
  historyStates.delete(key)
  historyStates.set(key, history)
  for (const oldest of historyStates.keys()) {
    if (historyStates.size <= HISTORY_NOTE_LIMIT) break
    historyStates.delete(oldest)
  }
}

/**
 * Import refuses a history captured from different content, which is exactly what a note
 * rewritten out of band (an agent edit, a conflict resolved from disk) produces. That
 * history describes bytes this note no longer has, so it is dropped rather than retried.
 */
function restoreHistory(key: string, element: ContinuityEditorElement): void {
  const history = historyStates.get(key)
  if (!history) return
  try {
    element.importHistory(history)
  } catch {
    historyStates.delete(key)
  }
}

type Props = {
  projectId: string
  resourceId: string
  initialText: string
  label?: string
  /** Host command-rail actions (Continuity shows its rail on touch); see `railActions` below. */
  railActions?: readonly RailAction[]
  /** Mirror of the live element so the pane header can read the engine's selection. */
  elementRef?: { current: ContinuityEditorElement | null }
}

/**
 * Track the user's note-editor preferences (Settings → Notes). They arrive as a
 * whole immutable object, so an editor re-renders only when one actually
 * changes — and `shortcutBindings` keeps its identity between changes, which
 * matters because the adapter re-assigns the binding overlay whenever that prop
 * is a new object.
 *
 * The two chords Continuity flags non-browser-safe but binds to the bullet and
 * task toggles are reclaimed by the default overlay (`config.py`): under
 * `browser-safe` policy an explicit override is checked before the release
 * filter, so those two are handled here while every other browser shortcut
 * (Ctrl+R reload, Ctrl+K, …) stays with the browser.
 */
function useNoteEditorSettings(): NoteEditorSettings {
  const [settings, setSettings] = useState(currentNoteEditorSettings)
  useEffect(() => {
    // Re-read on mount: the config fetch may have landed before this mounted.
    setSettings(currentNoteEditorSettings())
    const onChange = (event: Event) => setSettings((event as CustomEvent<NoteEditorSettings>).detail)
    window.addEventListener(NOTE_EDITOR_EVENT, onChange)
    return () => window.removeEventListener(NOTE_EDITOR_EVENT, onChange)
  }, [])
  return settings
}

/** Route the editor's host requests through the app's ordinary browser policies. */
function routeRequest(detail: ContinuityRequestDetail): void {
  if (detail.kind === 'openLink') {
    let target: URL | null = null
    try {
      target = new URL(detail.href, location.href)
    } catch {
      target = null
    }
    if (target && (target.protocol === 'http:' || target.protocol === 'https:')) {
      window.open(target.href, '_blank', 'noopener,noreferrer')
    }
    return
  }
  if (detail.kind === 'copyText') {
    // The editor asks the host only after its own Clipboard API and selection
    // fallbacks both failed (e.g. iOS Safari over plain-HTTP LAN), so a bare
    // clipboard retry here will usually fail too. Surface the text for manual
    // copy as the last resort.
    const manualCopy = () => {
      window.prompt('Copy the text below:', detail.text)
    }
    // Clipboard history still records it: this is exactly the case (insecure
    // context, denied policy) where the ring is the only way to get the text back.
    captureCopy(detail.text, 'note')
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(detail.text).catch(manualCopy)
    } else {
      manualCopy()
    }
  }
  // contextMenu and filesDropped fall through to default browser/daemon behavior.
}

/**
 * Answer the editor's `pasteText` request. The editor emits it only after its
 * own clipboard-read was refused (insecure context, or a webview/permission
 * policy that denies `clipboard-read`), so we read from the host and splice the
 * text via the editor's public `insertText`. When the host read is also blocked
 * we prompt for manual paste, mirroring the `copyText` fallback above, so
 * cross-application paste still works. In-editor copy/paste never reaches here
 * (the engine remembers its own last copy).
 */
async function handlePasteRequest(element: ContinuityEditorElement | null): Promise<void> {
  if (!element) return
  let text: string | null = null
  if (navigator.clipboard?.readText) {
    try {
      text = await navigator.clipboard.readText()
    } catch {
      text = null
    }
  }
  if (text === null) text = window.prompt('Paste text:')
  if (text) element.insertText(text)
}

/**
 * Shared Continuity WebAssembly markdown surface for every editable `.md` view: project
 * and session notes and markdown files opened from the Files browser, on desktop and mobile.
 *
 * The engine is seeded once (text `initialText`, revision 0) and then left uncontrolled: we
 * never push the engine's own `onChange` output back in as `value`/`revision`. Feeding it
 * back drove the React adapter to issue an async `replaceValue()` after every commit, and on
 * mobile that programmatic textarea rewrite lands mid-IME-composition (space commits a word),
 * desyncing the keyboard and re-injecting the document into itself — text multiplied on every
 * space. Nothing here needs the controlled loop: an out-of-band edit (daemon/agent rewrite)
 * never streams into a live editor; it arrives by remount with fresh `initialText`.
 *
 * How a committed snapshot is persisted is the caller's concern, passed as `onCommit`: notes
 * push to the autosave queue, while file editors only mark their draft dirty for an explicit
 * Save.
 *
 * Remount this (via a caller-supplied key) whenever a different document loads, so a fresh
 * engine cannot leak text or revisions between documents, and so external edits land.
 */
export function ContinuityMarkdownEditor({
  initialText,
  label,
  scrollKey,
  railActions,
  hostRef,
  onCommit,
}: {
  initialText: string
  label: string
  /** Stable per-resource identity for viewport restoration across unmounts; omit to disable. */
  scrollKey?: string
  /** Host actions appended to Continuity's own command rail (touch-primary devices show it).
   *  Assigning replaces the whole host set, so pass a stable array. */
  railActions?: readonly RailAction[]
  /** Mirror of the live element for hosts that need to read the engine's snapshot/selection
   *  from outside the editor (a header button takes DOM focus; the selection is engine state,
   *  so it survives that). Cleared on detach with the internal ref. */
  hostRef?: { current: ContinuityEditorElement | null }
  onCommit: (text: string) => void
}) {
  const elementRef = useRef<ContinuityEditorElement | null>(null)
  const hostRefTarget = useRef(hostRef)
  hostRefTarget.current = hostRef
  const scrollKeyRef = useRef(scrollKey)
  scrollKeyRef.current = scrollKey
  const settings = useNoteEditorSettings()
  // Capture the viewport at ref detach: Preact nulls refs before removing the DOM node, so
  // the textarea scroll offsets are still live here (they read 0 once detached).
  const attachRef = useCallback((element: ContinuityEditorElement | null) => {
    if (!element && elementRef.current) {
      if (scrollKeyRef.current) {
        scrollStates.set(scrollKeyRef.current, elementRef.current.getScrollState())
        stashHistory(scrollKeyRef.current, elementRef.current)
      }
      // Detached editors must not keep winning the insert routing.
      forgetEditorFocus(elementRef.current)
    }
    elementRef.current = element
    if (hostRefTarget.current) hostRefTarget.current.current = element
    // Focus inside the editor makes it the target for inserted text (clipboard
    // history, terminal selections) even after an overlay takes DOM focus.
    if (element) element.addEventListener('focusin', () => noteEditorFocus(element))
  }, [])
  return (
    <ContinuityEditor
      ref={attachRef}
      aria-label={label}
      class="note-editor"
      style={{
        display: 'block',
        flex: 1,
        width: '100%',
        height: '100%',
        minWidth: 0,
        minHeight: 0,
      }}
      // Seed only. These props are intentionally constant so the adapter never re-issues a
      // `replaceValue()` for our own edits — see the block comment above.
      value={initialText}
      revision={0}
      // User preferences (Settings → Notes). Unlike `value`/`revision` these are
      // live element configuration: the adapter re-applies them in place, so a
      // change never costs a remount (which would drop undo history and, worse,
      // reseed from a stale `initialText`).
      spellcheck={settings.spellcheck}
      syntax={settings.syntax}
      tabBehavior={settings.tabBehavior}
      shortcutPolicy={settings.shortcutPolicy}
      shortcutBindings={settings.shortcutBindings}
      indentGuides={settings.indentGuides}
      // Attribute rather than an adapter prop; `auto` is Continuity's own
      // touch-only default, so it is passed as undefined to leave it alone.
      command-rail={settings.commandRail === 'auto' ? undefined : settings.commandRail}
      railActions={railActions}
      onChange={(detail: ContinuityChangeDetail) => {
        // Uncontrolled: persist the committed snapshot, but never echo it back into `value`.
        // (`hostReplacement` cannot occur here since we issue no host replacements.)
        if (detail.source !== 'hostReplacement') onCommit(detail.snapshot.text)
      }}
      onReady={() => {
        const element = elementRef.current
        if (!element) return
        // History first: it is checked against the seeded text, and restoring the viewport
        // is unaffected either way.
        if (scrollKeyRef.current) restoreHistory(scrollKeyRef.current, element)
        const saved = scrollKeyRef.current && scrollStates.get(scrollKeyRef.current)
        if (saved) element.restoreScrollState(saved)
      }}
      onRequest={detail => {
        if (detail.kind === 'pasteText') {
          void handlePasteRequest(elementRef.current)
          return
        }
        routeRequest(detail)
      }}
    />
  )
}

/** Project/session note surface: Continuity committing through the resource-scoped autosave
 *  queue. Remounted (keyed by project/resource/load-generation) when a different note loads. */
export function ProjectNoteEditor({
  projectId,
  resourceId,
  initialText,
  label = 'Project note',
  railActions,
  elementRef,
}: Props) {
  const key = noteQueueKey(projectId, resourceId)
  return (
    <ContinuityMarkdownEditor
      initialText={initialText}
      label={label}
      scrollKey={key}
      railActions={railActions}
      hostRef={elementRef}
      onCommit={text => noteSaveQueue.submit(key, text)}
    />
  )
}
