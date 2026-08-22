/** Which files the UI offers "Preview in a pane" on.
 *
 * The list mirrors `STATIC_PREVIEW_ENTRY_SUFFIXES` in `project_files.py`, and it is
 * deliberately narrow. A static preview is a *page*: offering it on a stylesheet, a
 * lone image, or a markdown file would open a viewport showing something the file
 * tab already shows better, and the daemon would refuse the registration anyway.
 * Anything at all is still fetchable as a subresource of the page that is served.
 *
 * The predicate lives here rather than inline so both entry points — the file
 * browser's row menu and an open file's own header — agree on the answer, and so a
 * suffix added on the server has exactly one place to be added on the client.
 */
export const STATIC_PREVIEW_SUFFIXES = ['.html', '.htm', '.xhtml'] as const

export function isPreviewableDocument(path: string): boolean {
  const name = path.replace(/\\/g, '/').split('/').pop() || ''
  const dot = name.lastIndexOf('.')
  if (dot <= 0) return false
  const suffix = name.slice(dot).toLowerCase()
  return (STATIC_PREVIEW_SUFFIXES as readonly string[]).includes(suffix)
}
