import { useEffect, useState } from 'preact/hooks'
import type { ComponentType } from 'preact'
import type { CodeEditorProps } from './CodeEditor'

/**
 * The source editor, fetched the first time a file is opened for editing.
 *
 * CodeMirror's core — view, state, commands, language, autocomplete, search, plus this
 * app's theme layer — is the single largest thing in the bundle after the terminal, and
 * nothing in the workspace shell touches it until a resource is opened. Split out here it
 * loads on that click instead of on every page load, alongside the per-language grammars
 * `codeLanguage.ts` already fetches on demand.
 *
 * The placeholder deliberately reserves the editor's box rather than collapsing: the
 * chunk is local and usually resolves within a frame, and a zero-height stand-in makes
 * the surrounding layout jump on the fast path. `aria-busy` is what a screen reader gets
 * in that window; the message only becomes visible if the load is slow enough to matter.
 */
export function LazyCodeEditor(props: CodeEditorProps) {
  const [Editor, setEditor] = useState<ComponentType<CodeEditorProps> | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let live = true
    void import('./CodeEditor')
      .then(module => { if (live) setEditor(() => module.CodeEditor) })
      .catch(cause => { if (live) setError(cause instanceof Error ? cause.message : String(cause)) })
    return () => { live = false }
  }, [])
  if (error) return <div class="code-editor code-editor-state error" role="alert">Source editor unavailable: {error}</div>
  if (!Editor) return <div class="code-editor code-editor-state" aria-busy="true" aria-label={props.ariaLabel || props.filename}><span>Preparing editor…</span></div>
  return <Editor {...props} />
}
