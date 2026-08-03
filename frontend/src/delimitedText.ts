export const DELIMITED_MAX_ROWS = 10_000
export const DELIMITED_MAX_COLUMNS = 256
export const DELIMITED_MAX_CELLS = 100_000
export const DELIMITED_MAX_FIELD_CHARS = 16_384

export type DelimitedPreview = {
  rows: string[][]
  columnCount: number
  truncated: boolean
  malformed: boolean
  fieldTruncated: boolean
}

type DelimitedLimits = {
  rows?: number
  columns?: number
  cells?: number
  fieldChars?: number
}

/** RFC-4180-style parser with hard allocation bounds applied while parsing.
 *
 * The complete source text remains available in Raw mode. The preview stops before it can
 * materialize an adversarial number of strings, columns, or rows, and long fields keep only a
 * bounded display prefix.
 */
export function parseDelimitedText(
  text: string,
  delimiter: ',' | '\t',
  limits: DelimitedLimits = {},
): DelimitedPreview {
  const maxRows = Math.max(1, limits.rows ?? DELIMITED_MAX_ROWS)
  const maxColumns = Math.max(1, limits.columns ?? DELIMITED_MAX_COLUMNS)
  const maxCells = Math.max(1, limits.cells ?? DELIMITED_MAX_CELLS)
  const maxFieldChars = Math.max(1, limits.fieldChars ?? DELIMITED_MAX_FIELD_CHARS)
  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let fieldWasTruncated = false
  let fieldTruncated = false
  let truncated = false
  let malformed = false
  let inQuotes = false
  let quoteClosed = false
  let cells = 0
  let endedWithRecordSeparator = false

  const append = (value: string) => {
    endedWithRecordSeparator = false
    if (field.length < maxFieldChars) {
      const remaining = maxFieldChars - field.length
      field += value.slice(0, remaining)
      if (value.length > remaining) fieldWasTruncated = true
    } else {
      fieldWasTruncated = true
    }
    if (fieldWasTruncated) {
      fieldTruncated = true
      truncated = true
    }
  }
  const finishField = (): boolean => {
    quoteClosed = false
    if (row.length >= maxColumns) {
      truncated = true
      field = ''
      fieldWasTruncated = false
      return true
    }
    if (cells >= maxCells) {
      truncated = true
      return false
    }
    row.push(fieldWasTruncated ? `${field}…` : field)
    cells += 1
    field = ''
    fieldWasTruncated = false
    return true
  }
  const finishRow = (): boolean => {
    if (!finishField()) return false
    if (rows.length >= maxRows) {
      truncated = true
      return false
    }
    rows.push(row)
    row = []
    endedWithRecordSeparator = true
    return true
  }
  const result = (): DelimitedPreview => ({
    rows,
    columnCount: rows.reduce((maximum, item) => Math.max(maximum, item.length), 0),
    truncated,
    malformed,
    fieldTruncated,
  })

  if (!text.length) return result()
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index]
    if (inQuotes) {
      if (character === '"') {
        if (text[index + 1] === '"') {
          append('"')
          index += 1
        } else {
          inQuotes = false
          quoteClosed = true
        }
      } else if (character === '\r' || character === '\n') {
        if (character === '\r' && text[index + 1] === '\n') index += 1
        append('\n')
      } else {
        append(character)
      }
      continue
    }
    if (quoteClosed) {
      if (character === delimiter) {
        if (!finishField()) return result()
        endedWithRecordSeparator = false
        continue
      }
      if (character === '\r' || character === '\n') {
        if (character === '\r' && text[index + 1] === '\n') index += 1
        if (!finishRow()) return result()
        continue
      }
      // Preserve a malformed suffix in Raw mode and show the best bounded preview possible.
      malformed = true
      append(character)
      continue
    }
    if (character === delimiter) {
      if (!finishField()) return result()
      endedWithRecordSeparator = false
      continue
    }
    if (character === '\r' || character === '\n') {
      if (character === '\r' && text[index + 1] === '\n') index += 1
      if (!finishRow()) return result()
      continue
    }
    if (character === '"') {
      if (!field.length) inQuotes = true
      else {
        malformed = true
        append(character)
      }
      continue
    }
    append(character)
  }
  if (inQuotes) malformed = true
  if (!endedWithRecordSeparator && finishField()) {
    if (rows.length < maxRows) rows.push(row)
    else truncated = true
  }
  return result()
}
