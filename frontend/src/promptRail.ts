// Prompt-library templates as command-rail buttons.
//
// A 'prompt' rail item stores only the template's `scope:id` key, never its body:
// the library is the source of truth, so editing a template updates every button
// that points at it, and a rail item can never drift into a stale copy. The cost
// is that activation is asynchronous — the body is fetched at click time.
//
// Two outcomes, deliberately different:
//
//  * no `{{variables}}` → insert straight into the focused terminal. Insertion
//    never submits: the library's contract is that a template fills the composer
//    and the human presses Enter, and a rail button must not weaken that.
//  * has `{{variables}}` → there is nothing sensible to inject yet, so the button
//    opens the drawer's Prompts tab with the template preselected and its fields
//    expanded, which is the surface that already knows how to fill them.
//
// Both hosts (the strip in TerminalPane, the grid in CommandsTab) route through
// here, and both insert over the `mux:terminal-action` bus so the pane stays the
// single owner of terminal writes.

// The runtime import carries its extension so the pure helpers below stay importable
// by the node type-stripping test runner, which does not resolve extensionless paths.
import { api } from './api.ts'
import type { RailItem } from './commandRail'
import type { PromptTemplate } from './PromptLibrary'

/** Asks the app to open the Prompts tab on a specific template (variable filling). */
export const PROMPT_RAIL_EVENT = 'mux:open-prompt-template'

export type PromptLibraryResponse = { items: PromptTemplate[] }

/** Split a `scope:id` library key. Ids are UUIDs, so the first colon is the seam. */
export function splitPromptKey(key: string): { scope: string; id: string } | null {
  const seam = key.indexOf(':')
  if (seam <= 0 || seam === key.length - 1) return null
  return { scope: key.slice(0, seam), id: key.slice(seam + 1) }
}

/** The template a 'prompt' rail item points at, or null when it no longer exists.
 *  A same-id global/project conflict is resolved by the key's own scope, which is
 *  why the key (not the bare id) is what the item stores. */
export function findPromptTemplate(items: PromptTemplate[], key: string): PromptTemplate | null {
  return items.find(item => item.key === key) || null
}

/** Short settings/drawer caption for a prompt item: its title, or that it is dangling. */
export function promptItemSummary(item: RailItem, templates: PromptTemplate[] | null): string {
  if (!item.promptKey) return 'no template attached'
  if (!templates) return item.promptKey
  return findPromptTemplate(templates, item.promptKey)?.title || 'missing template'
}

export function fetchPromptTemplates(projectId?: string): Promise<PromptTemplate[]> {
  const scope = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
  return api<PromptLibraryResponse>('GET', `/api/prompts${scope}`).then(library => library.items || [])
}

/** Run a 'prompt' rail item. Resolves to '' on success, or to a message the caller
 *  shows in its own status line (the rail has no error surface of its own). */
export async function activatePromptRailItem(
  item: RailItem,
  ctx: { sessionId: string; projectId?: string },
): Promise<string> {
  const key = item.promptKey
  if (!key) return `“${item.label}” has no prompt template attached. Re-add it from Settings → Command rail.`
  let templates: PromptTemplate[]
  try {
    templates = await fetchPromptTemplates(ctx.projectId)
  } catch (cause) {
    return cause instanceof Error ? cause.message : 'Could not load prompt templates.'
  }
  const template = findPromptTemplate(templates, key)
  // A template can be deleted, or its Project scope switched off, long after a rail
  // button was made. Say which button is dangling rather than failing silently.
  if (!template) return `“${item.label}” points at a prompt template that is no longer available.`
  if (template.variables.length) {
    window.dispatchEvent(new CustomEvent(PROMPT_RAIL_EVENT, { detail: { key } }))
    return ''
  }
  window.dispatchEvent(new CustomEvent('mux:terminal-action', {
    detail: { sessionId: ctx.sessionId, action: 'insertText', text: template.body },
  }))
  // Recency/use counts are the library's own bounded state; a failure to record one
  // must not look like a failed insert.
  void api('POST', `/api/prompts/${template.scope}/${template.id}/use`, { project_id: ctx.projectId }).catch(() => {})
  return ''
}
