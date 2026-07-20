import { useEffect, useRef, useState } from 'preact/hooks'
import type {
  ContinuityChangeDetail,
  ContinuityEditorElement,
  ContinuityRequestDetail,
} from '@continuity-editor/editor'
import { ContinuityEditor } from '@continuity-editor/editor/react'
import { insertEditorTab, prefersPlainMobileEditor } from './editorText'
import { noteQueueKey, noteSaveQueue } from './noteSaveQueue'

type Props = {
  projectId: string
  resourceId: string
  initialText: string
  label?: string
}

// Continuity's defaults bind these to bullet / task toggles but flag them
// non-browser-safe, so `browser-safe` policy releases them to the browser
// (Ctrl+R reload, etc.). An explicit binding overlay is checked before that
// filter, so these two are handled (and preventDefault'd) while every other
// browser shortcut stays with the browser.
const NOTE_SHORTCUTS = {
  'mod+r': 'editor.toggle_bullet_at_line_start',
  'mod+e': 'markdown.toggle_task',
} as const

function usePlainMobileEditor(): boolean {
  const readPreference = () => prefersPlainMobileEditor(
    window.matchMedia('(max-width: 760px)').matches,
    window.matchMedia('(pointer: coarse)').matches,
  )
  const [preferred, setPreferred] = useState(readPreference)

  useEffect(() => {
    const narrow = window.matchMedia('(max-width: 760px)')
    const coarse = window.matchMedia('(pointer: coarse)')
    const update = () => setPreferred(prefersPlainMobileEditor(narrow.matches, coarse.matches))
    narrow.addEventListener('change', update)
    coarse.addEventListener('change', update)
    return () => {
      narrow.removeEventListener('change', update)
      coarse.removeEventListener('change', update)
    }
  }, [])

  return preferred
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
    void navigator.clipboard?.writeText(detail.text).catch(() => {})
  }
  // contextMenu and filesDropped fall through to default browser/daemon behavior.
}

/**
 * Project-note surface backed by the Continuity WebAssembly editor engine.
 *
 * Two revisions are kept strictly separate. `engine` is Continuity's in-memory
 * snapshot (its own revision starts at 0 for a freshly loaded note) and is the
 * only revision handed to the `<ContinuityEditor>` `revision` prop. The daemon's
 * optimistic storage revision lives entirely in the resource-scoped save queue
 * and is never passed here. On change we send only the text to the queue.
 *
 * This component is remounted (keyed by project/resource/load-generation) when a
 * different note loads, so switching notes constructs a fresh engine and cannot
 * leak text or revisions between notes.
 */
export function ProjectNoteEditor({ projectId, resourceId, initialText, label = 'Project note' }: Props) {
  const editorRef = useRef<ContinuityEditorElement>(null)
  const plainMobileEditor = usePlainMobileEditor()
  const [engine, setEngine] = useState<{ text: string; revision: number }>({
    text: initialText,
    revision: 0,
  })
  const key = noteQueueKey(projectId, resourceId)

  const commitUserText = (text: string) => {
    setEngine(current => ({ text, revision: current.revision + 1 }))
    noteSaveQueue.submit(key, text)
  }

  if (plainMobileEditor) {
    return <textarea
      class="note-editor mobile-note-editor"
      aria-label={label}
      defaultValue={engine.text}
      spellcheck={false}
      autoCapitalize="sentences"
      onInput={event => commitUserText(event.currentTarget.value)}
      onKeyDown={event => {
        if (event.key !== 'Tab') return
        event.preventDefault()
        const input = event.currentTarget
        const next = insertEditorTab(input.value, input.selectionStart, input.selectionEnd)
        input.value = next.text
        input.setSelectionRange(next.caret, next.caret)
        commitUserText(next.text)
      }}
    />
  }

  return (
    <ContinuityEditor
      ref={editorRef}
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
      value={engine.text}
      revision={engine.revision}
      spellcheck={false}
      shortcutPolicy="browser-safe"
      shortcutBindings={NOTE_SHORTCUTS}
      onChange={(detail: ContinuityChangeDetail) => {
        setEngine({ text: detail.snapshot.text, revision: detail.snapshot.revision })
        // A host replacement is an echo of text we pushed in; never save it back.
        if (detail.source !== 'hostReplacement') {
          noteSaveQueue.submit(key, detail.snapshot.text)
        }
      }}
      onRevisionConflict={() => {
        // Local typing won a race against a host replacement: keep local state.
        const current = editorRef.current?.snapshot()
        if (current) setEngine({ text: current.text, revision: current.revision })
      }}
      onRequest={routeRequest}
    />
  )
}
