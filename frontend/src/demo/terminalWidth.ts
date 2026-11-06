import stringWidth from 'string-width'

const segments = new Intl.Segmenter('en', { granularity: 'grapheme' })
export const graphemes = (text: string): string[] => Array.from(segments.segment(text), item => item.segment)
export const cellWidth = (text: string): number => stringWidth(text)
const display = (text: string): string => text.replaceAll('\n', '↵')

/** Bound the visible composer by terminal cells and keep its real caret in view. */
export function composerWindow(buffer: string, cursor: number, columns: number): { text: string; caret: number } {
  const before = graphemes(buffer.slice(0, cursor))
  let start = cursor
  let used = 0
  for (let index = before.length - 1; index >= 0; index -= 1) {
    const width = cellWidth(display(before[index]))
    if (used + width > columns) break
    start -= before[index].length
    used += width
  }
  let text = '', occupied = 0
  for (const cluster of graphemes(buffer.slice(start))) {
    const visible = display(cluster), width = cellWidth(visible)
    if (occupied + width > columns) break
    text += visible
    occupied += width
  }
  return { text, caret: used }
}

/** Wide glyphs wrap before a final single cell, leaving that cell unoccupied. */
export function wrappedRows(text: string, columns: number): number {
  let rows = 1, occupied = 0
  for (const cluster of graphemes(text)) {
    const width = cellWidth(cluster)
    if (width && occupied + width > columns) { rows += 1; occupied = 0 }
    occupied += width
  }
  return rows
}
