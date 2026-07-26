import { useEffect, useMemo, useState } from 'preact/hooks'
import { api } from './api'
import { renderPromptTemplate } from './promptTemplates'
import type { PromptTemplate } from './PromptLibrary'
import type { Project, ProjectBackend } from './types'

// Prompt templates, browse-and-insert only.
//
// Authoring stays in the full-screen library: a 380px column is the wrong place to
// write a multi-variable template, and splitting the editor across two hosts would
// mean maintaining two layouts of the same form. Inserting, which is the frequent
// action, belongs here next to clipboard history — both are "text into the focused
// session", and both should be one swipe away rather than three menu levels deep.

type Props = {
  project?: Project
  backend?: ProjectBackend
  onInsert: (text: string) => 'terminal' | 'editor' | 'none'
  onDone: () => void
  onManage: () => void
  /** A command-rail prompt button that needs placeholders filled hands the template
   *  off here. Wrapped in an object so re-firing the same button re-expands it. */
  preselect?: { key: string }
}

type Library = { items: PromptTemplate[] }

export function PromptsTab({ project, backend, onInsert, onDone, onManage, preselect }: Props) {
  const [items, setItems] = useState<PromptTemplate[] | null>(null)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [variables, setVariables] = useState<Record<string, string>>({})
  const [note, setNote] = useState('')

  useEffect(() => {
    const scope = project ? `?project_id=${encodeURIComponent(project.id)}` : ''
    api<Library>('GET', `/api/prompts${scope}`)
      .then(library => setItems(library.items))
      .catch(cause => setNote(cause instanceof Error ? cause.message : String(cause)))
  }, [project?.id])

  // Arriving from a rail button: expand that template's fields and clear any filter
  // that would hide it. The rail only hands off templates that *have* placeholders —
  // one without them was already inserted without opening this tab at all.
  useEffect(() => {
    if (!preselect || !items) return
    const target = items.find(item => item.key === preselect.key)
    if (!target) { setNote('That prompt template is no longer available.'); return }
    setQuery('')
    setSelected(target.key)
    setVariables(Object.fromEntries(target.variables.map(name => [name, ''])))
  }, [preselect, items])

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    return (items || []).filter(item =>
      (!backend || item.backends.includes(backend)) &&
      (!needle || `${item.title} ${item.tags.join(' ')} ${item.body}`.toLocaleLowerCase().includes(needle)))
  }, [items, query, backend])

  const active = filtered.find(item => item.key === selected) || null
  // A template with no placeholders is a one-tap insert; one with placeholders
  // expands its fields in place rather than opening a second surface.
  const missing = active?.variables.filter(name => !variables[name]?.trim()) || []

  const choose = (item: PromptTemplate) => {
    if (!item.variables.length) { void insert(item, item.body); return }
    setSelected(current => current === item.key ? null : item.key)
    setVariables(Object.fromEntries(item.variables.map(name => [name, ''])))
  }

  const insert = async (item: PromptTemplate, text: string) => {
    if (onInsert(text) === 'none') { setNote('Focus a terminal or note first, then insert.'); return }
    await api('POST', `/api/prompts/${item.scope}/${item.id}/use`, { project_id: project?.id }).catch(() => {})
    onDone()
  }

  return <>
    <p class="drawer-status">{project ? project.name : 'global templates'}{backend ? ` · ${backend}` : ''} · inserted, never sent</p>
    <div class="clipboard-search">
      <input value={query} onInput={event => setQuery(event.currentTarget.value)} placeholder="Filter templates…" aria-label="Filter prompt templates" />
    </div>
    <div class="clipboard-entries" role="group" aria-label="Prompt templates">
      {filtered.map(item => <div key={item.key} class={`clipboard-entry ${selected === item.key ? 'active' : ''}`}>
        <button class="clipboard-entry-body" title={item.variables.length ? 'Fill placeholders, then insert' : 'Insert into the focused terminal'} onClick={() => choose(item)}>
          <span>{item.favorite ? '★ ' : ''}{item.title}</span>
          <small>{item.scope}{item.tags.length ? ` · ${item.tags.join(', ')}` : ''}{item.variables.length ? ` · ${item.variables.length} field${item.variables.length === 1 ? '' : 's'}` : ''}</small>
        </button>
      </div>)}
      {active && active.variables.length > 0 && <div class="drawer-fields">
        {active.variables.map(name => <label key={name}>{name}
          <input value={variables[name] || ''} onInput={event => setVariables({ ...variables, [name]: event.currentTarget.value })} />
        </label>)}
        <button class="primary" disabled={missing.length > 0} title={missing.length ? `Fill: ${missing.join(', ')}` : 'Insert without submitting'} onClick={() => void insert(active, renderPromptTemplate(active.body, variables))}>Insert</button>
      </div>}
      {items && !filtered.length && <p class="drawer-empty">{items.length ? 'No template matches that filter.' : 'No prompt templates yet.'}</p>}
    </div>
    {note && <p class="clipboard-note" aria-live="polite">{note}</p>}
    <footer class="drawer-actions"><button onClick={onManage}>Manage templates…</button></footer>
  </>
}
