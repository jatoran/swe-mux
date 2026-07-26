// Clipboard payloads for project files: the two path forms and a bounded slice of file text.
//
// Pure so the joining and truncation rules can be unit-tested without a DOM or a server.

/** Project file paths travel over the API as posix-relative; the display/absolute form has to
 *  match the project root's own separator or it is not pasteable into that machine's shell. */
export function absoluteProjectPath(root: string, relativePath: string): string {
  const trimmed = relativePath.replace(/^[\\/]+/, '')
  // A root containing a backslash is Windows-shaped, including UNC (`\\host\share`). Drive
  // roots like `D:` with no separator count too, so the check is on the whole string.
  const windows = /\\/.test(root) || /^[A-Za-z]:$/.test(root)
  const base = root.replace(/[\\/]+$/, '')
  if (!trimmed) return base || root
  if (!base) return windows ? trimmed.replace(/\//g, '\\') : trimmed
  return windows ? `${base}\\${trimmed.replace(/\//g, '\\')}` : `${base}/${trimmed}`
}

// Bounds for "copy file contents". The server already refuses to send text above 2 MiB, so
// these only exist to keep a paste from being absurd: the clipboard itself handles megabytes
// fine, but the usual destination is an agent prompt, where every line is paid for. 5,000
// lines comfortably covers whole source files; the character bound is the backstop for
// minified or single-line files, where a line count means nothing.
export const FILE_COPY_MAX_LINES = 5_000
export const FILE_COPY_MAX_CHARS = 200_000

export type ClipboardSlice = {
  text: string
  truncated: boolean
  /** Lines/characters actually included, and what the file holds in total. */
  lines: number
  totalLines: number
  chars: number
  totalChars: number
}

const countLines = (text: string) => (text ? text.split('\n').length : 0)

/**
 * Cut `text` to the first `maxLines` lines and `maxChars` characters, whichever bites first,
 * and append a notice naming what was left behind. The notice matters because the copy is
 * usually pasted to an agent, which would otherwise read a truncated file as the whole file.
 */
export function truncateForClipboard(
  text: string,
  label: string,
  maxLines = FILE_COPY_MAX_LINES,
  maxChars = FILE_COPY_MAX_CHARS,
): ClipboardSlice {
  const totalLines = countLines(text)
  const totalChars = text.length
  let slice = text
  if (totalLines > maxLines) slice = text.split('\n').slice(0, maxLines).join('\n')
  if (slice.length > maxChars) {
    // Prefer cutting at a line boundary so the tail is never half a statement; fall back to a
    // hard cut when the overflow is one enormous line and there is no boundary to use.
    const hardCut = slice.slice(0, maxChars)
    const lastBreak = hardCut.lastIndexOf('\n')
    slice = lastBreak > 0 ? hardCut.slice(0, lastBreak) : hardCut
  }
  const lines = countLines(slice)
  if (slice.length === totalChars) {
    return { text: slice, truncated: false, lines, totalLines, chars: slice.length, totalChars }
  }
  const notice = `\n\n[swe-mux: truncated ${label} — copied ${lines.toLocaleString()} of ${totalLines.toLocaleString()} lines (${slice.length.toLocaleString()} of ${totalChars.toLocaleString()} characters)]\n`
  return {
    text: `${slice}${notice}`,
    truncated: true,
    lines,
    totalLines,
    chars: slice.length,
    totalChars,
  }
}

/** Human summary for the transient "copied" notice in the file browser. */
export function copySummary(slice: ClipboardSlice): string {
  if (!slice.truncated) {
    return `Copied ${slice.totalLines.toLocaleString()} line${slice.totalLines === 1 ? '' : 's'}`
  }
  return `Copied first ${slice.lines.toLocaleString()} of ${slice.totalLines.toLocaleString()} lines`
}
