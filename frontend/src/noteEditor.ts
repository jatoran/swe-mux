import { RangeSetBuilder } from '@codemirror/state'
import { Decoration, EditorView, ViewPlugin } from '@codemirror/view'
import type { DecorationSet, ViewUpdate } from '@codemirror/view'

/** Columns of leading whitespace on a line, expanding tabs to the next tab stop. */
export function indentColumns(text: string, tabSize: number): number {
  let columns = 0
  for (const char of text) {
    if (char === ' ') columns += 1
    else if (char === '\t') columns += tabSize - (columns % tabSize)
    else break
  }
  return columns
}

function indentDecorations(view: EditorView): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>()
  const tabSize = view.state.tabSize
  for (const { from, to } of view.visibleRanges) {
    for (let pos = from; pos <= to; ) {
      const line = view.state.doc.lineAt(pos)
      const columns = indentColumns(line.text, tabSize)
      if (columns > 0) {
        builder.add(
          line.from,
          line.from,
          Decoration.line({
            class: 'indented-wrapped-line',
            attributes: { style: `--indented:${columns}ch` },
          }),
        )
      }
      pos = line.to + 1
    }
  }
  return builder.finish()
}

/**
 * Hanging indent for soft-wrapped lines: a wrapped line continues at its own
 * leading indentation instead of resetting to column 0.
 *
 * A `<textarea>` cannot do this at all — it has no per-line boxes. CodeMirror
 * renders every line as its own element, so a line decoration can carry that
 * line's indent width as `--indented` and the CSS pair below turns it into a
 * hanging indent. The `border-left` + `::before` technique is deliberate:
 * `text-indent` breaks rectangular selection, and a border (rather than a
 * margin) keeps the active-line background covering the indented gutter.
 * See https://discuss.codemirror.net/t/making-codemirror-6-respect-indent-for-wrapped-lines/2881
 */
export const wrappedLineIndent = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet
    constructor(view: EditorView) {
      this.decorations = indentDecorations(view)
    }
    update(update: ViewUpdate) {
      if (update.docChanged || update.viewportChanged) {
        this.decorations = indentDecorations(update.view)
      }
    }
  },
  { decorations: plugin => plugin.decorations },
)

/**
 * Notes editor chrome. Font/size/weight/line-height are already forced onto every
 * non-xterm element by the app shell, so this only owns colour, padding, and the
 * wrapped-indent rules. `1ch` therefore resolves to exactly one monospace cell.
 */
export const noteEditorTheme = EditorView.theme({
  '&': { height: '100%', backgroundColor: 'var(--panel2)', color: 'var(--text)' },
  '&.cm-focused': { outline: 'none' },
  '.cm-scroller': { overflow: 'auto' },
  '.cm-content': { padding: '12px', caretColor: 'var(--text)', overflowWrap: 'anywhere' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--text)' },
  '.cm-placeholder': { color: 'var(--muted)' },
  '.indented-wrapped-line': { borderLeft: 'transparent solid calc(var(--indented))' },
  '.indented-wrapped-line::before': { content: '""', marginLeft: 'calc(-1 * var(--indented))' },
})
