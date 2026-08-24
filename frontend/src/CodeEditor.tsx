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
import { languageLoaderForFilename } from './codeLanguage'

/**
 * A CodeMirror 6 editor for source files — the syntax-highlighting replacement for the plain
 * `<textarea>` swe-mux used to show non-markdown text in. Prose and markdown still belong to the
 * Continuity editor; this is only for code and other plain-text files.
 *
 * The language is chosen from the filename once, at mount: the parent keys this component per
 * resource, so a different file is a different instance and the language (and undo history) never
 * bleed across files. `readOnly` and the external `value` are the only things reconciled live,
 * through a compartment and a doc-diff so neither loses cursor or history.
 *
 * The grammar itself is fetched on demand (`codeLanguage.ts`), so the view is created with the
 * document already in it and the language is reconfigured in when its chunk lands. That ordering
 * is deliberate: the alternative — awaiting the grammar before mounting — would replace the
 * first paint of a file with a spinner on every open, to buy syntax colours a frame earlier.
 */
export type CodeEditorProps = {
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
  const languageConf = useRef(new Compartment())
  // The last document this editor emitted through `onChange`. The parent stores exactly that
  // string and hands it straight back as `value`, so the reconcile effect below can dismiss
  // its own echo by comparing references, instead of serializing the whole document and
  // comparing it character by character once per keystroke.
  const lastEmitted = useRef<string | null>(null)
  // Emits that have not yet come back as `value`. A parent that stores the string and
  // re-renders is a turn behind the keyboard, so during a fast burst the effect below runs
  // with a document the editor has already moved past. Read as an external change, that is
  // a full replacement with an older copy of itself — the characters typed since are
  // discarded, the replacement re-emits, and the two chase each other. Counting the echoes
  // separates a lagging one from a genuine external rewrite, which no comparison of the
  // strings can do: both are simply "not the current document".
  const pendingEchoes = useRef(0)

  // Build the view once. `value`/`filename` are read from the initial render on purpose: later
  // changes are handled by the reconcile effects below (or a remount, when the parent re-keys).
  useEffect(() => {
    const parent = host.current
    if (!parent) return
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
        languageConf.current.of([]),
        codeThemeExtensions,
        readOnlyConf.current.of([
          EditorState.readOnly.of(readOnly),
          EditorView.editable.of(!readOnly),
        ]),
        EditorView.updateListener.of(update => {
          if (!update.docChanged) return
          const text = update.state.doc.toString()
          lastEmitted.current = text
          // Only an emit that a parent can echo counts: a read-only view has no handler,
          // so its own reconfigure must not leave a phantom echo owed.
          if (!onChangeRef.current) return
          pendingEchoes.current += 1
          onChangeRef.current(text)
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
    // The grammar arrives after the first paint, or not at all for a plain-text file. A
    // resource closed while its chunk is in flight must not reconfigure a destroyed view,
    // which is what `live` guards; a failed chunk fetch leaves the file as plain text,
    // exactly as an unknown extension does, rather than breaking the editor.
    let live = true
    const loader = languageLoaderForFilename(filename)
    if (loader) {
      void loader()
        .then(language => { if (live) created.dispatch({ effects: languageConf.current.reconfigure(language) }) })
        .catch(() => {})
    }
    return () => {
      live = false
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
  //
  // The echo is recognised by reference first. The parent stores the very string `onChange`
  // handed it, so on a keystroke `lastEmitted.current === value` is a pointer comparison, and
  // the document is never serialized a second time nor compared character by character. Only a
  // value this editor did not produce reaches `doc.toString()` — which is the case the
  // serialization was for.
  //
  // The second check is the one that keeps typing safe. Arriving at the current document
  // means every emit has been echoed, so the count resets; arriving at anything else while
  // emits are still outstanding means this is one of them, late, and the newer characters
  // it does not contain are not the editor's to discard.
  useEffect(() => {
    const current = view.current
    if (!current) return
    if (lastEmitted.current === value) { pendingEchoes.current = 0; return }
    if (pendingEchoes.current > 0) { pendingEchoes.current -= 1; return }
    const doc = current.state.doc.toString()
    if (doc !== value) current.dispatch({ changes: { from: 0, to: doc.length, insert: value } })
    lastEmitted.current = value
  }, [value])

  return <div class="code-editor" ref={host} />
}
