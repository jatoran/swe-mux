// CodeMirror styling wired to the app's theme.
//
// Every colour resolves to a CSS custom property, never a literal, so the editor tracks whichever
// of swe-mux's ~30 palettes (and light/dark) is active with no per-theme JS. Structural colours
// read the core palette vars (`--bg`, `--panel`, `--accent`, …) directly; token colours read the
// `--cm-*` vars that `style.css` maps onto that palette in one place. `color-mix` gives the
// translucent selection/active-line washes that read on any background.

import { EditorView } from '@codemirror/view'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { tags as t } from '@lezer/highlight'
import type { Extension } from '@codemirror/state'

const editorTheme = EditorView.theme({
  '&': { color: 'var(--text)', backgroundColor: 'var(--bg)', height: '100%' },
  '.cm-scroller': { fontFamily: "'Cascadia Mono', Consolas, monospace", lineHeight: '1.55' },
  '.cm-content': { caretColor: 'var(--accent)' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--accent)' },
  '.cm-selectionBackground, .cm-content ::selection': {
    backgroundColor: 'color-mix(in srgb, var(--accent) 26%, transparent)',
  },
  '&.cm-focused .cm-selectionBackground, &.cm-focused .cm-content ::selection': {
    backgroundColor: 'color-mix(in srgb, var(--accent) 32%, transparent)',
  },
  '.cm-gutters': {
    backgroundColor: 'var(--panel)',
    color: 'var(--muted)',
    border: 'none',
    borderRight: '1px solid var(--line)',
  },
  '.cm-activeLineGutter': { backgroundColor: 'var(--panel2)', color: 'var(--text)' },
  '.cm-activeLine': { backgroundColor: 'color-mix(in srgb, var(--panel2) 55%, transparent)' },
  '.cm-foldPlaceholder': {
    backgroundColor: 'var(--panel2)',
    color: 'var(--muted)',
    border: '1px solid var(--line)',
  },
  '.cm-matchingBracket, &.cm-focused .cm-matchingBracket': {
    backgroundColor: 'transparent',
    outline: '1px solid color-mix(in srgb, var(--accent) 55%, transparent)',
  },
  '.cm-nonmatchingBracket': { outline: '1px solid color-mix(in srgb, var(--red) 55%, transparent)' },
  '.cm-selectionMatch': { backgroundColor: 'color-mix(in srgb, var(--amber) 22%, transparent)' },
  '.cm-searchMatch': {
    backgroundColor: 'color-mix(in srgb, var(--amber) 28%, transparent)',
    outline: '1px solid color-mix(in srgb, var(--amber) 55%, transparent)',
  },
  '.cm-searchMatch.cm-searchMatch-selected': {
    backgroundColor: 'color-mix(in srgb, var(--accent) 42%, transparent)',
  },
  '.cm-panels': { backgroundColor: 'var(--panel)', color: 'var(--text)' },
  '.cm-panels.cm-panels-top': { borderBottom: '1px solid var(--line)' },
  '.cm-panels.cm-panels-bottom': { borderTop: '1px solid var(--line)' },
  '.cm-panel.cm-search input': {
    backgroundColor: 'var(--bg)',
    color: 'var(--text)',
    border: '1px solid var(--line)',
  },
  '.cm-panel.cm-search button': {
    backgroundColor: 'var(--panel2)',
    color: 'var(--text)',
    border: '1px solid var(--line)',
  },
  '.cm-panel.cm-search label': { color: 'var(--muted)' },
  '.cm-tooltip': {
    backgroundColor: 'var(--panel)',
    color: 'var(--text)',
    border: '1px solid var(--line)',
  },
  '.cm-tooltip .cm-tooltip-arrow:before': { borderTopColor: 'var(--line)', borderBottomColor: 'var(--line)' },
  '.cm-tooltip .cm-tooltip-arrow:after': { borderTopColor: 'var(--panel)', borderBottomColor: 'var(--panel)' },
})

const codeHighlight = HighlightStyle.define([
  {
    tag: [
      t.keyword,
      t.modifier,
      t.controlKeyword,
      t.operatorKeyword,
      t.definitionKeyword,
      t.moduleKeyword,
    ],
    color: 'var(--cm-keyword)',
  },
  {
    tag: [t.comment, t.lineComment, t.blockComment, t.docComment],
    color: 'var(--cm-comment)',
    fontStyle: 'italic',
  },
  { tag: [t.string, t.special(t.string), t.docString, t.character], color: 'var(--cm-string)' },
  { tag: [t.regexp, t.escape], color: 'var(--cm-string)' },
  {
    tag: [t.number, t.integer, t.float, t.bool, t.null, t.atom, t.unit],
    color: 'var(--cm-number)',
  },
  {
    tag: [t.self, t.constant(t.variableName), t.standard(t.variableName)],
    color: 'var(--cm-number)',
  },
  {
    tag: [t.function(t.variableName), t.function(t.propertyName), t.labelName, t.macroName],
    color: 'var(--cm-function)',
  },
  {
    tag: [t.typeName, t.className, t.namespace, t.definition(t.typeName)],
    color: 'var(--cm-type)',
  },
  { tag: [t.propertyName], color: 'var(--cm-property)' },
  { tag: [t.attributeName], color: 'var(--cm-attribute)' },
  { tag: [t.tagName, t.angleBracket], color: 'var(--cm-tag)' },
  { tag: [t.variableName], color: 'var(--cm-variable)' },
  {
    tag: [
      t.operator,
      t.derefOperator,
      t.punctuation,
      t.separator,
      t.bracket,
      t.paren,
      t.squareBracket,
      t.brace,
    ],
    color: 'var(--cm-punctuation)',
  },
  { tag: [t.meta, t.processingInstruction, t.annotation, t.documentMeta], color: 'var(--cm-meta)' },
  { tag: [t.heading, t.heading1, t.heading2, t.heading3, t.heading4], color: 'var(--cm-heading)', fontWeight: 'bold' },
  { tag: [t.strong], fontWeight: 'bold' },
  { tag: [t.emphasis], fontStyle: 'italic' },
  { tag: [t.strikethrough], textDecoration: 'line-through' },
  { tag: [t.link, t.url], color: 'var(--cm-link)', textDecoration: 'underline' },
  { tag: [t.invalid], color: 'var(--cm-invalid)' },
])

/** Theme plus syntax colours, one extension to add to every code editor. */
export const codeThemeExtensions: Extension = [editorTheme, syntaxHighlighting(codeHighlight)]
