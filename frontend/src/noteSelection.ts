// Turning a Continuity selection into something an agent can be handed.
//
// Continuity reports selections as `{line, byteInLine}` pairs — UTF-8 byte offsets *inside a
// line* — and exposes no `getSelectedText()`, so the slicing is ours: every byte offset has to
// be walked back to a UTF-16 index before a JavaScript string can be cut. The selection lives
// in the engine, not in DOM focus, which is why a button in the pane header (which takes focus
// when clicked) can still read exactly what the user highlighted.
//
// Pure, and with no runtime import from the editor package, so the node type-stripping test
// runner can load it: the snapshot shape is restated structurally below and is assignable from
// the editor's own `Snapshot`.

export type SelectionPosition = { line: number; byteInLine: number }
export type SelectionKind = 'caret' | 'lineWise' | 'blockWise'
export type EditorSelection = {
  anchor: SelectionPosition
  head: SelectionPosition
  kind: SelectionKind
}
export type EditorSnapshot = { text: string; selections: readonly EditorSelection[] }

/** What the message was built from: a highlighted range, or the document for want of one. */
export type MessageScope = 'selection' | 'document'
export type MessageSource = { label: string; scope: MessageScope }

const utf8Size = (codePoint: number) =>
  codePoint < 0x80 ? 1 : codePoint < 0x800 ? 2 : codePoint < 0x10000 ? 3 : 4

/**
 * The UTF-16 index inside `line` that `byteInLine` UTF-8 bytes reach.
 *
 * An offset landing inside a multi-byte character clamps to that character's start rather than
 * splitting it: a half-encoded astral character would corrupt the payload, and the editor only
 * ever reports boundaries anyway, so the clamp is a guard, not a behaviour.
 */
export function utf16IndexForByte(line: string, byteInLine: number): number {
  if (byteInLine <= 0) return 0
  let bytes = 0
  let index = 0
  while (index < line.length) {
    const codePoint = line.codePointAt(index)
    if (codePoint === undefined) break
    const size = utf8Size(codePoint)
    if (bytes + size > byteInLine) return index
    bytes += size
    index += codePoint > 0xffff ? 2 : 1
    if (bytes === byteInLine) return index
  }
  return line.length
}

/**
 * The UTF-8 byte offset inside `line` reached by `index` UTF-16 units — the inverse of
 * `utf16IndexForByte`.
 *
 * Needed in the other direction by anything that locates text with JavaScript's own string
 * search and then has to describe it to the engine: `String.indexOf` answers in UTF-16
 * units, and every Continuity position is a byte offset.
 *
 * An index landing between the halves of a surrogate pair counts the whole character
 * rather than half of it, matching the forward direction's clamp.
 */
export function byteForUtf16Index(line: string, index: number): number {
  if (index <= 0) return 0
  let bytes = 0
  let cursor = 0
  while (cursor < line.length && cursor < index) {
    const codePoint = line.codePointAt(cursor)
    if (codePoint === undefined) break
    bytes += utf8Size(codePoint)
    cursor += codePoint > 0xffff ? 2 : 1
  }
  return bytes
}

/** Document order for two selection ends (a selection may be anchored after its head). */
export function comparePositions(a: SelectionPosition, b: SelectionPosition): number {
  return a.line !== b.line ? a.line - b.line : a.byteInLine - b.byteInLine
}

const orderedEnds = (selection: EditorSelection): [SelectionPosition, SelectionPosition] =>
  comparePositions(selection.anchor, selection.head) <= 0
    ? [selection.anchor, selection.head]
    : [selection.head, selection.anchor]

const clampLine = (lines: string[], line: number) =>
  Math.max(0, Math.min(line, Math.max(0, lines.length - 1)))

/** Flat UTF-16 offset into the whole document for a line/byte position. */
function flatIndex(lines: string[], position: SelectionPosition): number {
  const line = clampLine(lines, position.line)
  let offset = 0
  for (let index = 0; index < line; index++) offset += lines[index].length + 1
  return offset + utf16IndexForByte(lines[line] ?? '', position.byteInLine)
}

/** Rectangular selection: the same byte columns taken out of every covered line. */
function blockText(lines: string[], start: SelectionPosition, end: SelectionPosition): string {
  const fromByte = Math.min(start.byteInLine, end.byteInLine)
  const toByte = Math.max(start.byteInLine, end.byteInLine)
  const first = clampLine(lines, start.line)
  const last = clampLine(lines, end.line)
  const rows: string[] = []
  for (let line = first; line <= last; line++) {
    const source = lines[line] ?? ''
    rows.push(source.slice(utf16IndexForByte(source, fromByte), utf16IndexForByte(source, toByte)))
  }
  return rows.join('\n')
}

/**
 * Is anything actually highlighted?
 *
 * Structural on purpose: this answers a Continuity `isEnabled` predicate, which is
 * re-evaluated on every commit and every selection change, so it must not slice a large
 * document on each keystroke.
 */
export function hasSelection(snapshot: EditorSnapshot | null | undefined): boolean {
  return (snapshot?.selections || []).some(
    selection =>
      selection.kind === 'lineWise' ||
      selection.anchor.line !== selection.head.line ||
      selection.anchor.byteInLine !== selection.head.byteInLine,
  )
}

/** The highlighted text, or '' when the selection is a bare caret. Multiple selections join
 *  with newlines in the order the editor reports them. */
export function selectionText(snapshot: EditorSnapshot | null | undefined): string {
  if (!snapshot) return ''
  const lines = snapshot.text.split('\n')
  const parts: string[] = []
  for (const selection of snapshot.selections || []) {
    const [start, end] = orderedEnds(selection)
    if (selection.kind === 'blockWise') {
      parts.push(blockText(lines, start, end))
      continue
    }
    if (selection.kind === 'lineWise') {
      parts.push(lines.slice(clampLine(lines, start.line), clampLine(lines, end.line) + 1).join('\n'))
      continue
    }
    const from = flatIndex(lines, start)
    const to = flatIndex(lines, end)
    if (to > from) parts.push(snapshot.text.slice(from, to))
  }
  return parts.filter(part => part !== '').join('\n')
}

/**
 * The message an agent receives. The body keeps its leading whitespace (indentation carries
 * meaning in a code block) and loses only its trailing run, so the composer is not left with a
 * blank tail. The origin line is the agent's only context for a fragment that arrives with no
 * file attached.
 */
export function composeAgentMessage(body: string, source: MessageSource): string {
  const origin =
    source.scope === 'selection'
      ? `From \`${source.label}\` (selected text):`
      : `From \`${source.label}\`:`
  return `${origin}\n\n${body.replace(/\s+$/, '')}`
}

// New-session seeding travels as `seed_text` on the spawn request: the daemon inlines short
// bodies into the agent CLI's argv (with the leading-dash guard) and stages over-bound ones
// into the workspace with a reader prompt, so the browser carries no length ceiling.

// Filling a *live* session's composer goes over `POST /api/sessions/{id}/input`, which writes
// raw bytes: a multi-line body sent unwrapped would submit at every newline. xterm's own paste
// path (what the terminal rail uses) wraps in bracketed paste, so the same wrapping is rebuilt
// here for the writes that never pass through a mounted pane. Agent TUIs have bracketed paste
// on, which is why only Claude/Codex sessions are offered as targets. Actual *delivery*
// (paste + submit) is the queue's send operation, performed daemon-side.
export const BRACKETED_PASTE_START = '\x1b[200~'
export const BRACKETED_PASTE_END = '\x1b[201~'

/** Newlines become CR inside the block, matching what xterm writes for a real paste. */
export function pastePayload(message: string): string {
  const normalized = message.replace(/\r\n?/g, '\n').replace(/\n/g, '\r')
  return `${BRACKETED_PASTE_START}${normalized}${BRACKETED_PASTE_END}`
}
