import { useEffect, useRef } from 'preact/hooks'
import {
  EditorView,
  keymap,
  lineNumbers,
  highlightActiveLine,
  highlightActiveLineGutter,
  drawSelection,
  dropCursor,
  rectangularSelection,
  crosshairCursor,
} from '@codemirror/view'
import { EditorState, Compartment } from '@codemirror/state'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { indentOnInput, bracketMatching, foldGutter, foldKeymap } from '@codemirror/language'
import { closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete'
import { searchKeymap, highlightSelectionMatches } from '@codemirror/search'
import { codeThemeExtensions } from './codeTheme'
import { languageForFilename } from './codeLanguage'

/**
 * A CodeMirror 6 editor for source files — the syntax-highlighting replacement for the plain
 * `<textarea>` swe-mux used to show non-markdown text in. Prose and markdown still belong to the
 * Continuity editor; this is only for code and other plain-text files.
 *
 * The language is chosen from the filename once, at mount: the parent keys this component per
 * resource, so a different file is a different instance and the language (and undo history) never
 * bleed across files. `readOnly` and the external `value` are the only things reconciled live,
 * through a compartment and a doc-diff so neither loses cursor or history.
 */
type CodeEditorProps = {
  /** Current document text (the controlled value). */
  value: string
  /** File name or path — drives language selection and the accessible label. */
  filename: string
  /** Read-only view (a file the daemon will not let us write). */
  readOnly?: boolean
  /** Called with the full document on every user edit. Omit for read-only views. */
  onChange?: (text: string) => void
  /** Accessible label; defaults to the filename. */
  ariaLabel?: string
}

export function CodeEditor({ value, filename, readOnly = false, onChange, ariaLabel }: CodeEditorProps) {
  const host = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
  // Read through refs so the single long-lived view always calls the latest handler and never
  // has to be rebuilt when a prop identity changes.
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange
  const readOnlyConf = useRef(new Compartment())

  // Build the view once. `value`/`filename` are read from the initial render on purpose: later
  // changes are handled by the reconcile effects below (or a remount, when the parent re-keys).
  useEffect(() => {
    const parent = host.current
    if (!parent) return
    const language = languageForFilename(filename)
    const state = EditorState.create({
      doc: value,
      extensions: [
        lineNumbers(),
        highlightActiveLineGutter(),
        highlightActiveLine(),
        foldGutter(),
        history(),
        drawSelection(),
        dropCursor(),
        EditorState.allowMultipleSelections.of(true),
        indentOnInput(),
        bracketMatching(),
        closeBrackets(),
        rectangularSelection(),
        crosshairCursor(),
        highlightSelectionMatches(),
        keymap.of([
          ...closeBracketsKeymap,
          ...defaultKeymap,
          ...searchKeymap,
          ...historyKeymap,
          ...foldKeymap,
          indentWithTab,
        ]),
        ...(language ? [language] : []),
        codeThemeExtensions,
        readOnlyConf.current.of([
          EditorState.readOnly.of(readOnly),
          EditorView.editable.of(!readOnly),
        ]),
        EditorView.updateListener.of(update => {
          if (update.docChanged) onChangeRef.current?.(update.state.doc.toString())
        }),
        EditorView.contentAttributes.of({
          'aria-label': ariaLabel || filename,
          spellcheck: 'false',
          autocapitalize: 'off',
          autocorrect: 'off',
        }),
      ],
    })
    const created = new EditorView({ state, parent })
    view.current = created
    return () => {
      created.destroy()
      view.current = null
    }
    // Mount-only: the reconcile effects below carry every later change. filename is stable for a
    // given instance because the parent keys this component per resource.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Toggle read-only in place, preserving history and scroll.
  useEffect(() => {
    view.current?.dispatch({
      effects: readOnlyConf.current.reconfigure([
        EditorState.readOnly.of(readOnly),
        EditorView.editable.of(!readOnly),
      ]),
    })
  }, [readOnly])

  // Reconcile an externally-changed value (reload from disk, conflict overwrite). Skipped when the
  // incoming value already equals the editor's own doc — which is the case for the edit we just
  // emitted through onChange — so typing never round-trips into a cursor-resetting replacement.
  useEffect(() => {
    const current = view.current
    if (!current) return
    const doc = current.state.doc.toString()
    if (doc === value) return
    current.dispatch({ changes: { from: 0, to: doc.length, insert: value } })
  }, [value])

  return <div class="code-editor" ref={host} />
}
