import { useRef, useState } from 'preact/hooks'
import type {
  ContinuityChangeDetail,
  ContinuityEditorElement,
  ContinuityRequestDetail,
} from '@continuity-editor/editor'
import { ContinuityEditor } from '@continuity-editor/editor/react'
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
 * Shared Continuity WebAssembly markdown surface for every editable `.md` view: project
 * and session notes and markdown files opened from the Files browser, on desktop and mobile.
 *
 * It owns only Continuity's in-memory revision (starting at 0 for a freshly loaded
 * document); the daemon's optimistic storage revision never reaches here. How a committed
 * snapshot is persisted is the caller's concern, passed as `onCommit`: notes push to the
 * autosave queue, while file editors only mark their draft dirty for an explicit Save. A
 * host replacement is an echo of text we pushed in and is never committed back.
 *
 * Remount this (via a caller-supplied key) whenever a different document loads, so a fresh
 * engine cannot leak text or revisions between documents.
 */
export function ContinuityMarkdownEditor({
  initialText,
  label,
  spellcheck = false,
  onCommit,
}: {
  initialText: string
  label: string
  spellcheck?: boolean
  onCommit: (text: string) => void
}) {
  const editorRef = useRef<ContinuityEditorElement>(null)
  const [engine, setEngine] = useState<{ text: string; revision: number }>({
    text: initialText,
    revision: 0,
  })

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
      spellcheck={spellcheck}
      shortcutPolicy="browser-safe"
      shortcutBindings={NOTE_SHORTCUTS}
      onChange={(detail: ContinuityChangeDetail) => {
        setEngine({ text: detail.snapshot.text, revision: detail.snapshot.revision })
        // A host replacement is an echo of text we pushed in; never commit it back.
        if (detail.source !== 'hostReplacement') onCommit(detail.snapshot.text)
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

/** Project/session note surface: Continuity committing through the resource-scoped autosave
 *  queue. Remounted (keyed by project/resource/load-generation) when a different note loads. */
export function ProjectNoteEditor({ projectId, resourceId, initialText, label = 'Project note' }: Props) {
  const key = noteQueueKey(projectId, resourceId)
  return (
    <ContinuityMarkdownEditor
      initialText={initialText}
      label={label}
      onCommit={text => noteSaveQueue.submit(key, text)}
    />
  )
}
