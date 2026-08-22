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
//    opens Prompt templates in the Actions drawer with the template preselected and its fields
//    expanded, which is the surface that already knows how to fill them.
//
// Both hosts (the strip in TerminalPane, the Quick actions grid in Actions) route through
// here, and both insert over the `mux:terminal-action` bus so the pane stays the
// single owner of terminal writes.

// The runtime import carries its extension so the pure helpers below stay importable
// by the node type-stripping test runner, which does not resolve extensionless paths.
import { api } from './api.ts'
import type { RailItem } from './commandRail'
import type { PromptTemplate } from './promptTemplates'
import { insertIntoTerminal } from './terminalActions.ts'

/** Asks the app to open Prompt templates in Actions (variable filling). */
export const PROMPT_RAIL_EVENT = 'mux:open-prompt-template'

export type PromptLibraryResponse = { items: PromptTemplate[] }

export type PromptRailActivation =
  | { status: 'inserted' }
  | { status: 'fields' }
  | { status: 'error'; message: string }

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

/**
 * What a button is called.
 *
 * For a prompt item pinned without a name of its own, that is the template's
 * *live* title: the stored `label` was a snapshot taken at pin time, and a
 * snapshot of a name is exactly the kind of copy this item type exists to avoid —
 * the body is a pointer, so the name should be one too. Renaming a template
 * therefore renames its buttons.
 *
 * A label somebody typed (`autoLabel` absent) is never overridden, and the stored
 * copy remains the fallback while the library has not been read yet or the
 * template has gone missing — the dangling case is reported when the button is
 * *pressed*, where there is room to say which template it wanted.
 */
export function railItemLabel(item: RailItem, templates: PromptTemplate[] | null): string {
  if (item.type !== 'prompt' || !item.autoLabel || !item.promptKey || !templates) return item.label
  return findPromptTemplate(templates, item.promptKey)?.title || item.label
}

/** Read the library for one scope: global alone, or global plus that Project's own.
 *
 *  Deliberately never the widened management listing (`all_projects=1`). This is
 *  the read an Action layout resolves a pin against, and confining it is what stops
 *  a *global* layout pinning a Project template — a button that would insert
 *  nothing in every other Project (`prompt-library.md`). */
export function fetchPromptTemplates(projectId?: string): Promise<PromptTemplate[]> {
  const scope = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
  return api<PromptLibraryResponse>('GET', `/api/prompts${scope}`).then(library => library.items || [])
}

/** Record a use. Bounded library state, never the point of the click: a failure
 *  to count must not read as a failed insert. */
export function recordPromptUse(template: PromptTemplate, projectId?: string): void {
  void api('POST', `/api/prompts/${template.scope}/${template.id}/use`, {
    project_id: template.project_id || projectId,
  }).catch(() => {})
}

/**
 * Run one template against a session: insert it, or hand off to the fields UI.
 *
 * `insert` is how the text reaches the terminal. Inside the pane that is the
 * pane's own handle; from the drawer it is the `mux:terminal-action` bus, which
 * is the same thing one hop further out — either way the pane stays the single
 * owner of terminal writes.
 */
export async function activatePromptTemplate(
  template: PromptTemplate,
  ctx: { projectId?: string; insert: (text: string) => void | Promise<void> },
): Promise<PromptRailActivation> {
  if (template.variables.length) {
    window.dispatchEvent(new CustomEvent(PROMPT_RAIL_EVENT, { detail: { key: template.key } }))
    return { status: 'fields' }
  }
  try {
    await ctx.insert(template.body)
  } catch (cause) {
    return { status: 'error', message: cause instanceof Error ? cause.message : String(cause) }
  }
  recordPromptUse(template, ctx.projectId)
  return { status: 'inserted' }
}

/** Run a prompt rail item, distinguishing insertion from a variable-field handoff. */
export async function activatePromptRailItem(
  item: RailItem,
  ctx: { sessionId: string; projectId?: string },
): Promise<PromptRailActivation> {
  const key = item.promptKey
  if (!key) return { status: 'error', message: `“${item.label}” has no prompt template attached. Re-add it from Configure Actions.` }
  let templates: PromptTemplate[]
  try {
    templates = await fetchPromptTemplates(ctx.projectId)
  } catch (cause) {
    return { status: 'error', message: cause instanceof Error ? cause.message : 'Could not load prompt templates.' }
  }
  const template = findPromptTemplate(templates, key)
  // A template can be deleted, or its Project scope switched off, long after a rail
  // button was made. Say which button is dangling rather than failing silently.
  if (!template) return { status: 'error', message: `“${item.label}” points at a prompt template that is no longer available.` }
  return activatePromptTemplate(template, {
    projectId: ctx.projectId,
    // `insertIntoTerminal` rather than a bare dispatch: it carries a request id and
    // waits for the pane's acknowledgement, which is what turns a refusal - an
    // unmounted pane, or a session showing an approval dialog - into the `error`
    // status a button can render. A fire-and-forget dispatch reported every insert
    // as done, including the ones that never happened.
    insert: text => insertIntoTerminal(ctx.sessionId, text, false),
  })
}
