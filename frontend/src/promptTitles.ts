// Live titles for the prompt buttons that do not carry a name of their own.
//
// A `prompt` Action item stores the template's `scope:id` key and nothing else,
// so a button pinned without a typed label has no title until the library is
// read (`railItemLabel`). The surfaces that draw those buttons — the rail strip,
// Quick actions, both editors — render synchronously from the Action config and
// have no business each opening their own request for the same list.
//
// So this is one lazily-filled cache, keyed by Project scope, shared by all of
// them. Three properties are the point:
//
//  * **Nothing is fetched unless something needs it.** `usePromptTitles` takes a
//    `needed` flag, which every caller derives from whether the rows it is about
//    to draw actually contain an auto-labelled prompt button. A rail with no
//    prompt buttons — the overwhelmingly common case — costs nothing at all.
//  * **One request per scope**, deduped while in flight, so eight panes mounting
//    at once make one call.
//  * **A write anywhere on this device invalidates it**, over the same local event
//    the drawer and the library already use. A title edited on *another* device is
//    stale here until something reloads, which is the honest bound: the daemon
//    does not push template changes, and inventing a poll for a button caption
//    would cost more than the caption is worth.

import { useEffect, useState } from 'preact/hooks'

import { PROMPT_LIBRARY_CHANGED_EVENT } from './promptLibraryEvents'
import { fetchPromptTemplates } from './promptRail'
import type { PromptTemplate } from './promptTemplates'

/** Fired when a scope's list lands, so mounted readers re-render. */
export const PROMPT_TITLES_EVENT = 'mux:prompt-titles'

const cache = new Map<string, PromptTemplate[]>()
const inflight = new Map<string, Promise<unknown>>()

const scopeKey = (projectId?: string): string => projectId || ''

/** What is already known for a scope, or null when it has never been read. */
export function cachedPromptTemplates(projectId?: string): PromptTemplate[] | null {
  return cache.get(scopeKey(projectId)) ?? null
}

/** Read a scope once. Repeat calls while a read is in flight join it. */
export function ensurePromptTitles(projectId?: string): void {
  const key = scopeKey(projectId)
  if (cache.has(key) || inflight.has(key)) return
  const request = fetchPromptTemplates(projectId)
    .then(items => { cache.set(key, items) })
    // A failure caches "nothing known", which renders every button's stored label
    // — the correct fallback — and, crucially, terminates: leaving the scope
    // uncached would have the completion event re-trigger the read that just
    // failed, forever. The next library write (or reload) tries again.
    .catch(() => { cache.set(key, []) })
    .finally(() => {
      inflight.delete(key)
      window.dispatchEvent(new CustomEvent(PROMPT_TITLES_EVENT, { detail: { key } }))
    })
  inflight.set(key, request)
}

/** Drop every scope. Called on any local library write. */
export function invalidatePromptTitles(): void {
  cache.clear()
  window.dispatchEvent(new CustomEvent(PROMPT_TITLES_EVENT, { detail: { key: null } }))
}

let wired = false
/** Installed once, from the first reader: a library write on this device makes
 *  every cached title suspect. */
function wireInvalidation(): void {
  if (wired) return
  wired = true
  window.addEventListener(PROMPT_LIBRARY_CHANGED_EVENT, () => invalidatePromptTitles())
}

/**
 * The templates a surface may resolve titles against, or null while unknown.
 *
 * `needed` is what keeps this free for the surfaces that have no prompt buttons:
 * pass false and nothing is requested, nothing is subscribed, and the caller's
 * stored labels stand.
 */
export function usePromptTitles(projectId: string | undefined, needed: boolean): PromptTemplate[] | null {
  const key = scopeKey(projectId)
  const [templates, setTemplates] = useState<PromptTemplate[] | null>(() => (needed ? cachedPromptTemplates(projectId) : null))
  useEffect(() => {
    if (!needed) { setTemplates(null); return }
    wireInvalidation()
    const sync = () => {
      ensurePromptTitles(projectId)
      setTemplates(cachedPromptTemplates(projectId))
    }
    sync()
    window.addEventListener(PROMPT_TITLES_EVENT, sync)
    return () => window.removeEventListener(PROMPT_TITLES_EVENT, sync)
  }, [key, needed])
  return templates
}
