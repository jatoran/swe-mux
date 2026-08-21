import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'preact/hooks'
import type {
  ContinuityChangeDetail,
  ContinuityEditorElement,
  ContinuityRequestDetail,
  HistoryState,
  RailAction,
  ScrollState,
} from '@continuity-editor/editor'
import { ContinuityEditor } from '@continuity-editor/editor/react'
import { whenLayoutBox } from './layoutBox'
import { noteQueueKey, noteSaveQueue } from './noteSaveQueue'
import { claimEditorInsertTarget, forgetEditorFocus, noteEditorFocus } from './insertTarget'
import type { EditorHandle, TextSurfaceIdentity } from './insertTarget'
import { captureCopy } from './clipboardHistory'
import { hasSelection, selectionText } from './noteSelection'
import { createNoteRailIcon } from './noteRailIcons'
import {
  currentNoteEditorSettings,
  ensureNoteRailArrangement,
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

/**
 * Editors whose host let go of them before the engine finished starting. Continuity rejects
 * `ready` on teardown ("destroyed before becoming ready"), which is ordinary lifecycle - a
 * drawer opened and closed again, a tab switched during WASM initialization - and not
 * something to report.
 */
const abandonedEditors = new WeakSet<ContinuityEditorElement>()

/**
 * The events that prove a human is editing *this* note, as opposed to the engine re-emitting
 * what it was handed. `isTrusted` is the whole point: a browser sets it only for events it
 * generated from real input, so nothing this app (or the editor) dispatches can forge one.
 *
 * Pointer contact counts. It is not a text change, but it cannot be produced by a save loop
 * either, and Continuity's own command rail edits through taps that emit no key event - a
 * bullet toggled right after a reload would otherwise be mistaken for an echo and dropped.
 *
 * All composed events, so they cross the editor's shadow boundary to the host element, and all
 * captured, so a handler inside the editor cannot stop this from seeing them.
 */
const LOCAL_INPUT_EVENTS = [
  'pointerdown',
  'keydown',
  'beforeinput',
  'paste',
  'cut',
  'drop',
  'compositionstart',
] as const

function watchLocalInput(element: ContinuityEditorElement, report: () => void): void {
  const onInput = (event: Event) => {
    if (event.isTrusted) report()
  }
  for (const name of LOCAL_INPUT_EVENTS) {
    element.addEventListener(name, onInput, { capture: true })
  }
}

/**
 * The editor as an insert target, wrapped so that text arriving from elsewhere in the app -
 * the clipboard-history picker, a terminal selection, voice Append - is announced as local
 * input before it lands.
 *
 * Those inserts are the user's doing, but the event that proves it happened on some other
 * element, so nothing on this editor would report them. Without the wrapper a paste that
 * followed a reload would be judged an echo and silently dropped.
 *
 * Memoized per element because insert-target identity is compared, not structurally matched
 * (`insertTarget.ts`), and `isConnected` is a getter so a detached editor still refuses.
 */
const insertHandles = new WeakMap<ContinuityEditorElement, EditorHandle>()

function insertHandle(element: ContinuityEditorElement, report: () => void): EditorHandle {
  const existing = insertHandles.get(element)
  if (existing) return existing
  const handle: EditorHandle = {
    insertText: text => {
      report()
      element.insertText(text)
    },
    get isConnected() { return element.isConnected },
  }
  insertHandles.set(element, handle)
  return handle
}

/**
 * Observe the engine's start so a failure to start is reported as itself.
 *
 * `ready` rejects when the first render throws, and nothing else in this host or in the
 * React adapter attaches a handler to it: the rejection reached `App`'s global
 * `unhandledrejection` backstop instead, which toasted the raw DOM message ("Cannot read
 * properties of null (reading 'offsetLeft')") at the user with no hint of what had failed.
 * The mount gate below removes the known cause; this makes any remaining one legible.
 */
function watchEditorStart(element: ContinuityEditorElement): void {
  void element.ready.catch((cause: unknown) => {
    if (abandonedEditors.has(element)) return
    const message = cause instanceof Error ? cause.message : String(cause)
    console.error('[note-editor] Continuity failed to start', cause)
    window.dispatchEvent(new CustomEvent('mux:error', { detail: `The note editor failed to start: ${message}` }))
  })
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
  /** App-level identity used by dictation and other focus-following input surfaces. */
  textSurface?: TextSurfaceIdentity
  /** Claim routing without raising the mobile keyboard. Consumed once by the caller. */
  claimInsertTargetToken?: number
  onInsertTargetClaimed?: (token: number) => void
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

/** App clipboard policy shared by Continuity's fallback request and the explicit rail action. */
function copyNoteText(text: string): void {
  const manualCopy = () => { window.prompt('Copy the text below:', text) }
  captureCopy(text, 'note')
  if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).catch(manualCopy)
  else manualCopy()
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
  if (detail.kind === 'copyText') copyNoteText(detail.text)
  // contextMenu and filesDropped fall through to default browser/daemon behavior.
}

/**
 * Answer either the editor's fallback request or the explicit rail Paste action.
 * Clipboard denial falls back to the rail's remembered copy when available, then to a manual
 * prompt; accepted text is spliced through the editor's public `insertText` operation.
 */
async function handlePasteRequest(element: ContinuityEditorElement | null, remembered = ''): Promise<void> {
  if (!element) return
  let text: string | null = null
  if (navigator.clipboard?.readText) {
    try {
      text = await navigator.clipboard.readText()
    } catch {
      text = null
    }
  }
  if (text === null && remembered) text = remembered
  if (text === null) text = window.prompt('Paste text:')
  if (text) element.insertText(text)
}

// Continuity's internal clipboard is private to its native selection action bar. Keep the
// rail's last successful copy as the same-browser fallback when clipboard-read is denied.
let noteRailClipboard = ''
const NOTE_CLIPBOARD_RAIL_ACTIONS: readonly RailAction[] = [
  {
    id: 'mux:copy',
    label: 'Copy selected text',
    icon: () => createNoteRailIcon('copy'),
    isEnabled: snapshot => hasSelection(snapshot),
    run: editor => {
      const text = selectionText(editor.snapshot())
      if (!text) return
      noteRailClipboard = text
      copyNoteText(text)
    },
  },
  {
    id: 'mux:paste',
    label: 'Paste text',
    icon: () => createNoteRailIcon('paste'),
    run: editor => { void handlePasteRequest(editor, noteRailClipboard) },
  },
]

/**
 * Shared Continuity WebAssembly markdown surface for every editable `.md` view: project
 * and Markdown files opened from the Files browser, on desktop and mobile.
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
  textSurface,
  claimInsertTargetToken,
  onInsertTargetClaimed,
  onCommit,
  onLocalInput,
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
  textSurface?: TextSurfaceIdentity
  claimInsertTargetToken?: number
  onInsertTargetClaimed?: (token: number) => void
  onCommit: (text: string) => void
  /** Called for every trusted user input on this editor; see `watchLocalInput` above. */
  onLocalInput?: () => void
}) {
  ensureNoteRailArrangement()
  const elementRef = useRef<ContinuityEditorElement | null>(null)
  const selectionActionsObserver = useRef<MutationObserver | null>(null)
  const hostRefTarget = useRef(hostRef)
  hostRefTarget.current = hostRef
  const scrollKeyRef = useRef(scrollKey)
  scrollKeyRef.current = scrollKey
  const textSurfaceRef = useRef(textSurface)
  textSurfaceRef.current = textSurface
  const localInputRef = useRef(onLocalInput)
  localInputRef.current = onLocalInput
  const settings = useNoteEditorSettings()
  const resolvedRailActions = useMemo<readonly RailAction[]>(
    () => [...NOTE_CLIPBOARD_RAIL_ACTIONS, ...(railActions || [])],
    [railActions],
  )
  // Capture the viewport at ref detach: Preact nulls refs before removing the DOM node, so
  // the textarea scroll offsets are still live here (they read 0 once detached).
  const attachRef = useCallback((element: ContinuityEditorElement | null) => {
    if (!element && elementRef.current) {
      abandonedEditors.add(elementRef.current)
      selectionActionsObserver.current?.disconnect()
      selectionActionsObserver.current = null
      elementRef.current.removeAttribute('data-selection-actions-open')
      if (scrollKeyRef.current) {
        scrollStates.set(scrollKeyRef.current, elementRef.current.getScrollState())
        stashHistory(scrollKeyRef.current, elementRef.current)
      }
      // Detached editors must not keep winning the insert routing.
      forgetEditorFocus(insertHandles.get(elementRef.current) ?? elementRef.current)
    }
    elementRef.current = element
    if (hostRefTarget.current) hostRefTarget.current.current = element
    // Focus inside the editor makes it the target for inserted text (clipboard
    // history, terminal selections) even after an overlay takes DOM focus.
    if (element) {
      const report = () => localInputRef.current?.()
      element.addEventListener('focusin', () => noteEditorFocus(insertHandle(element, report), textSurfaceRef.current))
      watchLocalInput(element, report)
      watchEditorStart(element)
    }
  }, [])
  useEffect(() => {
    const element = elementRef.current
    if (element?.matches(':focus-within')) noteEditorFocus(insertHandle(element, () => localInputRef.current?.()), textSurface)
  }, [textSurface?.id, textSurface?.kind, textSurface?.label])
  useEffect(() => {
    const element = elementRef.current
    claimEditorInsertTarget(element&&insertHandle(element,()=>localInputRef.current?.()),textSurface,claimInsertTargetToken,onInsertTargetClaimed)
  },[claimInsertTargetToken,textSurface?.id,textSurface?.kind,textSurface?.label,onInsertTargetClaimed])
  /**
   * The engine is not created while its slot has no layout box.
   *
   * Continuity's first render positions the copy control for every inline-code span off
   * `span.offsetParent`, which is null for every node inside a `display:none` subtree. Started
   * hidden, the editor therefore threw a TypeError out of its async start: `ready` rejected
   * (surfacing as an app-wide "Cannot read properties of null (reading 'offsetLeft')" toast),
   * `continuity-ready` never fired, and the scroll and undo-history restore that hang off it
   * never ran. The utility drawer hits this every time it opens on a tab other than Notes,
   * because it keeps its note host mounted-but-`hidden` so an in-progress note survives a tab
   * switch; a note pane hidden by mobile's focused-pane rule or by desktop zoom is the same
   * shape.
   *
   * Only the *first* render needs a box, so this gate is one-way: once the element exists it
   * stays mounted through any later hiding, which is exactly the invariant the drawer's
   * mounted-but-hidden host depends on.
   */
  const slotRef = useRef<HTMLDivElement>(null)
  const [engineSlotReady, setEngineSlotReady] = useState(false)
  useLayoutEffect(() => {
    if (engineSlotReady) return
    const slot = slotRef.current
    if (!slot) return
    return whenLayoutBox(slot, () => setEngineSlotReady(true))
  }, [engineSlotReady])
  // A layout effect, and a placeholder that occupies the editor's own grid cell, so a slot
  // that is visible on first paint swaps to the editor within that same frame.
  if (!engineSlotReady) return <div ref={slotRef} class="note-editor-slot" aria-hidden="true" style={{
    display: 'block',
    flex: 1,
    width: '100%',
    height: '100%',
    minWidth: 0,
    minHeight: 0,
  }} />
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
      railActions={resolvedRailActions}
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
        // The selection toolbar is inside Continuity's paint-contained shadow tree, while the
        // heading trail is a sibling overlay. Mirror toolbar visibility onto the host so CSS can
        // promote that whole stacking context only for the lifetime of the popup.
        const selectionActions = element.shadowRoot?.querySelector<HTMLElement>('.selection-actions')
        if (selectionActions) {
          const syncSelectionActions = () => element.toggleAttribute(
            'data-selection-actions-open',
            !selectionActions.hidden,
          )
          selectionActionsObserver.current?.disconnect()
          selectionActionsObserver.current = new MutationObserver(syncSelectionActions)
          selectionActionsObserver.current.observe(selectionActions, {
            attributes: true,
            attributeFilter: ['hidden'],
          })
          syncSelectionActions()
        }
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

/** Project-owned note surface: Continuity committing through the resource-scoped autosave
 *  queue. Remounted (keyed by project/resource/load-generation) when a different note loads. */
export function ProjectNoteEditor({
  projectId,
  resourceId,
  initialText,
  label = 'Note',
  railActions,
  elementRef,
  textSurface,
  claimInsertTargetToken,
  onInsertTargetClaimed,
}: Props) {
  const key = noteQueueKey(projectId, resourceId)
  return (
    <ContinuityMarkdownEditor
      initialText={initialText}
      label={label}
      scrollKey={key}
      railActions={railActions}
      hostRef={elementRef}
      textSurface={textSurface}
      claimInsertTargetToken={claimInsertTargetToken}
      onInsertTargetClaimed={onInsertTargetClaimed}
      onCommit={text => noteSaveQueue.submit(key, text)}
      onLocalInput={() => noteSaveQueue.markLocalInput(key)}
    />
  )
}
