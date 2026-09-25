/** Where a Preview pane is, and where it should be.
 *
 *  A pane cannot read its sandboxed document's location, so the runtime bridge the
 *  daemon injects into every preview page posts it (`assets/preview/runtime_bridge.js`).
 *  That message is written by the previewed page, which is untrusted code, so every
 *  path in it is re-validated here before the pane mounts a URL built from it. */

/** Dispatched with `{ previewId }` when the user opens a link to a preview's page, so
 *  a pane that has navigated elsewhere returns to it even if the page is unchanged. */
export const PREVIEW_OPEN_PAGE_EVENT = 'mux:preview-open-page'

/** How often a live loopback pane asks whether its page's bytes changed. */
export const PREVIEW_LIVE_POLL_MS = 2000

/** The document plus the assets it loaded - the daemon's revision check bound. */
export const PREVIEW_REVISION_MAX_PATHS = 24

const SCHEME = /^[a-z][a-z0-9+.-]*:/i

/** The route a preview is served under: `/preview/<id>/`. */
export const previewRoute = (previewId: string) => `/preview/${encodeURIComponent(previewId)}/`

/** A page path relative to a preview's route, or null when it is not one.
 *  Anything that would resolve outside the route - a scheme, a host, a leading
 *  slash, a `..` walk - is refused rather than repaired. */
export function previewPagePath(previewId: string, value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 2048) return null
  if (/[\u0000-\u001f]/.test(value)) return null
  if (value.startsWith('/') || value.startsWith('\\') || SCHEME.test(value)) return null
  const route = previewRoute(previewId)
  let resolved: URL
  try { resolved = new URL(route + value, 'http://preview.invalid') } catch { return null }
  if (resolved.origin !== 'http://preview.invalid' || !resolved.pathname.startsWith(route)) return null
  return value
}

export type PreviewLocation = { path: string; resources: string[] }

/** The bridge's location report, validated, or null for any other message. */
export function readPreviewLocation(previewId: string, data: unknown): PreviewLocation | null {
  if (!data || typeof data !== 'object') return null
  const message = data as { source?: unknown; type?: unknown; path?: unknown; resources?: unknown }
  if (message.source !== 'swe-mux-preview' || message.type !== 'location') return null
  const path = previewPagePath(previewId, message.path)
  if (path === null) return null
  const resources: string[] = []
  if (Array.isArray(message.resources)) {
    for (const item of message.resources) {
      if (resources.length >= PREVIEW_REVISION_MAX_PATHS - 1) break
      const resource = previewPagePath(previewId, item)
      if (resource !== null && resource !== path && !resources.includes(resource)) resources.push(resource)
    }
  }
  return { path, resources }
}

/** The paths a revision check fingerprints: the page itself, then what it loaded.
 *  A fragment never reaches a server, so it is not part of what is compared. */
export function revisionPaths(location: PreviewLocation): string[] {
  const page = location.path.split('#', 1)[0]
  return [page, ...location.resources.filter(item => item !== page)].slice(0, PREVIEW_REVISION_MAX_PATHS)
}
