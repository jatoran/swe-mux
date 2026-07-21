import type {
  ContinuityChangeDetail,
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
  spellcheck = false,
  onCommit,
}: {
  initialText: string
  label: string
  spellcheck?: boolean
  onCommit: (text: string) => void
}) {
  return (
    <ContinuityEditor
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
      spellcheck={spellcheck}
      shortcutPolicy="browser-safe"
      shortcutBindings={NOTE_SHORTCUTS}
      onChange={(detail: ContinuityChangeDetail) => {
        // Uncontrolled: persist the committed snapshot, but never echo it back into `value`.
        // (`hostReplacement` cannot occur here since we issue no host replacements.)
        if (detail.source !== 'hostReplacement') onCommit(detail.snapshot.text)
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
