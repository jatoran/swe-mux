import { useEffect, useState } from 'preact/hooks'

import { RailDropup } from './RailDropup'
import { subscribeToPromptLibraryChanges } from './promptLibraryEvents'
import { activatePromptTemplate, fetchPromptTemplates } from './promptRail'
import {
  orderPromptTemplates, promptTemplateExcerpt, promptTemplateRef, type PromptTemplate,
} from './promptTemplates'

// The prompt library as the rail sees it: the templates this session could use,
// one tap from the composer they are going into.
//
// The third picker with the Clip/Skills shape, and it exists for the same reason
// those two do. A template already reachable in two taps from a pinned button was
// never the problem; the templates *nobody pinned* were, because reaching one of
// those meant opening the drawer, finding the section, and scrolling it. Pinning
// still earns a template its own dedicated button — this is the way to the rest.
//
// Ordering is favourites, then most recently used, then title. That is the
// library's own notion of "relevant", not a second one: `favorite` and
// `last_used_at` are what the drawer section already sorts and badges by.
//
// Two exits, because this shortcut has two full surfaces behind it: the drawer
// section (browse, fill fields, pin) and the library modal opened straight into
// create mode, which is where "I want a template for this" actually goes.

type Props = {
  projectId?: string
  /** Harness the session runs, so templates that declare backends are filtered. */
  backend: string
  anchor: HTMLElement | null
  onClose: () => void
  /** Insert into the pane this rail belongs to. Never submits — a template fills
   *  the composer and the human presses Enter. */
  onInsert: (text: string) => Promise<void>
  /** Open the Prompt templates section of the Actions drawer. */
  onOpenSection: () => void
  /** Open the prompt library in create mode. */
  onCreate: () => void
}

export function PromptsDropup({ projectId, backend, anchor, onClose, onInsert, onOpenSection, onCreate }: Props) {
  const [templates, setTemplates] = useState<PromptTemplate[] | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')

  useEffect(() => {
    let live = true
    const load = () => fetchPromptTemplates(projectId)
      .then(items => { if (live) { setTemplates(items); setError('') } })
      .catch(cause => { if (live) setError(cause instanceof Error ? cause.message : String(cause)) })
    void load()
    // A template edited anywhere else on this device is a template this list is
    // wrong about, and the list is open for seconds at a time.
    const stop = subscribeToPromptLibraryChanges(() => { void load() })
    return () => { live = false; stop() }
  }, [projectId])

  // Keyed by `promptTemplateRef`, not by `key`: a global and a Project template may
  // share one stable id, and the library returns both under one `scope:id`.
  const run = async (template: PromptTemplate) => {
    setBusy(promptTemplateRef(template))
    setError('')
    const result = await activatePromptTemplate(template, { projectId, insert: onInsert })
    setBusy('')
    if (result.status === 'error') { setError(result.message); return }
    // A template with fields handed off to the drawer, which is now the surface
    // to look at; either way this shortcut is done.
    onClose()
  }

  // Backend filtering matches the drawer section: a template declaring backends
  // means them, and one declaring none is text that suits any composer.
  const rows = orderPromptTemplates((templates || [])
    .filter(item => !item.backends.length || (item.backends as readonly string[]).includes(backend)))
  return <RailDropup
    label="Prompt templates"
    anchor={anchor}
    onClose={onClose}
    sticky={[
      {
        label: 'All prompt templates…',
        title: 'Open the Prompt templates section of the Actions drawer, where templates can be searched, filled in and pinned',
        run: onOpenSection,
      },
      { label: '+ New', title: 'Open the prompt library on a new template', run: onCreate },
    ]}
  >
    {!templates && !error && <p class="rail-dropup-empty">Reading the prompt library…</p>}
    {error && <p class="rail-dropup-note" role="alert">{error}</p>}
    {templates && !rows.length && <p class="rail-dropup-empty">
      {templates.length ? 'No template applies to this session type.' : 'No prompt templates yet. “+ New” starts one.'}
    </p>}
    {rows.map(template => <button
      key={promptTemplateRef(template)}
      type="button"
      class="rail-dropup-row"
      disabled={!!busy}
      title={`${template.title}\n${promptTemplateExcerpt(template.body, 200)}${template.variables.length ? `\nOpens Prompt templates to fill: ${template.variables.join(', ')}` : '\nInserts without sending'}`}
      onClick={() => void run(template)}
    >
      <span>
        {template.favorite && <code>★</code>}
        {busy === promptTemplateRef(template) ? 'Inserting…' : template.title}
        {/* A template with fields cannot be injected as it stands, so the button
            says where the tap actually goes before it goes there. */}
        {!!template.variables.length && <b class="skill-flag">{template.variables.length} field{template.variables.length === 1 ? '' : 's'}</b>}
      </span>
      <small>{template.project_name || template.scope} · {promptTemplateExcerpt(template.body, 64)}</small>
    </button>)}
  </RailDropup>
}
