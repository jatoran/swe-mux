import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { notifyPromptLibraryChanged, subscribeToPromptLibraryChanges } from './promptLibraryEvents'
import { promptTemplateRef, renderPromptTemplate, type PromptTemplate } from './promptTemplates'
import { PromptDraftActions, PromptDraftFields, usePromptDraft } from './PromptTemplateEditor'
import type { Project, ProjectBackend } from './types'
import { useDismissLevel } from './modalFocus'

export type { PromptTemplate } from './promptTemplates'

// The wide view. What it is *for* — and the only thing the Actions drawer cannot
// also do since editing moved there — is breadth: comparing templates side by
// side, and reaching one whose Project is not the focused one.
//
// Selecting a template puts it straight into an editable form. There is no Edit
// mode to enter: the previous read-only pane made every wording fix a two-click
// detour into a different layout of the same fields, and the read-only rendering
// showed nothing the editable one does not.

type Library = {
  configured_scope: string
  items: PromptTemplate[]
  projects: Array<{ id: string; name: string }>
  diagnostics: Array<{ path: string; error: string }>
}

export function PromptLibrary({ project, backend, onInsert, onClose }: {
  project?: Project
  backend?: ProjectBackend
  onInsert: (text: string) => void
  onClose: () => void
}) {
  const [library, setLibrary] = useState<Library | null>(null)
  const [query, setQuery] = useState('')
  const [fleet, setFleet] = useState(false)
  const [selectedRef, setSelectedRef] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [createNonce, setCreateNonce] = useState(0)
  const [variables, setVariables] = useState<Record<string, string>>({})
  const [confirmClose, setConfirmClose] = useState(false)
  const [note, setNote] = useState('')
  const search = useRef<HTMLInputElement>(null)

  // A save notifies every prompt view *and* reloads this one, so two loads can be
  // in flight at once with different selections to keep. Newest issued wins.
  const generation = useRef(0)
  const projectArg = project ? `project_id=${encodeURIComponent(project.id)}` : ''
  const load = (keepRef?: string | null) => {
    const params = [projectArg, fleet ? 'all_projects=1' : ''].filter(Boolean).join('&')
    const mine = ++generation.current
    return api<Library>('GET', `/api/prompts${params ? `?${params}` : ''}`)
      .then(next => {
        if (mine !== generation.current) return
        setLibrary(next)
        setSelectedRef(current => {
          const wanted = keepRef !== undefined ? keepRef : current
          const match = next.items.find(item => promptTemplateRef(item) === wanted)
          return match ? promptTemplateRef(match) : next.items[0] ? promptTemplateRef(next.items[0]) : null
        })
      })
      .catch(cause => { if (mine === generation.current) setNote(cause instanceof Error ? cause.message : String(cause)) })
  }
  useEffect(() => { void load(); search.current?.focus() }, [project?.id, fleet])
  // A write from anywhere else (the drawer's inline editor, an Action pin) must
  // land here too, the same way this panel's own writes reach the drawer.
  useEffect(() => subscribeToPromptLibraryChanges(() => { void load() }), [project?.id, fleet])

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    return (library?.items || []).filter(item =>
      (!backend || item.backends.includes(backend)) &&
      (!needle || `${item.title} ${item.tags.join(' ')} ${item.body} ${item.project_name || ''}`.toLocaleLowerCase().includes(needle)))
  }, [library, query, backend])

  const selected = creating ? null : filtered.find(item => promptTemplateRef(item) === selectedRef)
    || (library?.items || []).find(item => promptTemplateRef(item) === selectedRef)
    || null

  // Projects that accept a Project-scoped template: the daemon lists exactly the
  // ones it read, so a Project whose `prompt_library_scope` excludes them is
  // absent and never offered as an owner.
  const owners = library?.projects || []

  const editor = usePromptDraft({
    template: selected,
    project,
    owners,
    resetKey: creating ? `new:${createNonce}` : '',
    onSaved: saved => { setCreating(false); void load(promptTemplateRef(saved)) },
    onDeleted: () => { setCreating(false); void load(null) },
  })

  useEffect(() => setVariables(current =>
    Object.fromEntries(editor.variables.map(name => [name, current[name] || '']))), [editor.variables.join(' ')])

  const close = () => { if (editor.dirty) { setConfirmClose(true); return } onClose() }
  // `close` is the panel's dismiss even though a dirty draft makes it raise the unsaved
  // decision instead of closing: that decision opens as a level above, so the next back
  // answers it rather than closing the panel underneath it.
  useDismissLevel(close, true, 'prompt-library')
  useDismissLevel(() => setConfirmClose(false), confirmClose, 'prompt-close-confirm')

  const preview = renderPromptTemplate(editor.draft.body, variables)
  const missing = editor.variables.filter(name => !variables[name]?.trim())

  const favorite = async (item: PromptTemplate) => {
    await api('PATCH', `/api/prompts/${item.scope}/${item.id}/favorite`, {
      project_id: item.project_id || project?.id,
      favorite: !item.favorite,
    })
    notifyPromptLibraryChanged()
    await load()
  }

  const insert = async () => {
    if (!selected || missing.length) return
    // The unsaved body is what the preview shows, so inserting it while the draft
    // is dirty would paste text no template holds. Save first, or discard.
    if (editor.dirty) { setNote('Save or revert this template before inserting it.'); return }
    onInsert(preview)
    await api('POST', `/api/prompts/${selected.scope}/${selected.id}/use`, { project_id: selected.project_id || project?.id })
      .then(() => notifyPromptLibraryChanged()).catch(() => {})
    onClose()
  }

  const beginCreate = () => { setCreating(true); setCreateNonce(value => value + 1); setNote('') }
  const pick = (item: PromptTemplate) => {
    if (editor.dirty) { setNote('Save or revert the open template before switching.'); return }
    setCreating(false); setSelectedRef(promptTemplateRef(item)); setNote('')
  }

  return <div class="modal-layer prompt-library-layer" onMouseDown={event => event.target === event.currentTarget && close()}>
    <section class="prompt-library" role="dialog" aria-modal="true" aria-label="Prompt library">
      <header>
        <div>
          <strong>PROMPT LIBRARY</strong>
          <small>{project ? project.name : 'global templates'} · text is inserted, never sent</small>
        </div>
        <button aria-label="Close prompt library" onClick={close}>×</button>
      </header>
      <div class="prompt-library-body">
        <aside>
          <div class="prompt-search">
            <input ref={search} value={query} onInput={event => setQuery(event.currentTarget.value)} placeholder="Search prompts…" />
            <button title="New template" aria-label="New template" onClick={beginCreate}>+</button>
          </div>
          <label class="prompt-scope-filter">
            <input type="checkbox" checked={fleet} onChange={event => setFleet(event.currentTarget.checked)} />
            <span>All Projects</span>
          </label>
          {creating && <button class="active" disabled aria-current="true"><span>New template</span><small>unsaved</small></button>}
          {filtered.map(item => <button
            key={promptTemplateRef(item)}
            class={!creating && promptTemplateRef(item) === selectedRef ? 'active' : ''}
            onClick={() => pick(item)}
          >
            <span>{item.favorite ? '★ ' : '☆ '}{item.title}</span>
            <small>{item.project_name || item.scope}{item.conflict ? ' · ID CONFLICT' : ''}{item.tags.length ? ` · ${item.tags.join(', ')}` : ''}</small>
          </button>)}
          {!filtered.length && <p>No compatible prompts.</p>}
        </aside>
        <main>
          {(selected || creating) ? <>
            <header>
              <div>
                <strong>{editor.draft.title || (creating ? 'New template' : 'Untitled')}</strong>
                <small>{editor.draft.scope}{selected?.project_name ? ` · ${selected.project_name}` : ''} · {editor.draft.backends.join(' · ') || 'no backends'}</small>
              </div>
              {selected && <div>
                <button aria-label="Toggle favorite" onClick={() => void favorite(selected)}>{selected.favorite ? '★' : '☆'}</button>
              </div>}
            </header>
            {selected?.conflict && <p class="settings-inline-error">A global and Project template share this stable ID. Choose by scope or edit one ID on disk.</p>}
            <PromptDraftFields state={editor} owners={owners} />
            <PromptDraftActions state={editor} onCancel={creating ? () => setCreating(false) : undefined} cancelLabel="Discard" />
            {editor.message && <p class="prompt-draft-note" aria-live="polite">{editor.message}</p>}
            {selected && <div class="prompt-fill">
              {editor.variables.map(name => <label key={name}>{name}
                <input value={variables[name] || ''} onInput={event => setVariables({ ...variables, [name]: event.currentTarget.value })} />
              </label>)}
              <label>Preview<textarea readOnly rows={10} value={preview} /></label>
              <div class="prompt-actions">
                <button
                  class="primary"
                  disabled={missing.length > 0 || editor.dirty}
                  title={editor.dirty ? 'Save or revert first' : missing.length ? `Fill: ${missing.join(', ')}` : 'Insert without submitting'}
                  onClick={() => void insert()}
                >Insert into terminal</button>
                <button onClick={() => void navigator.clipboard.writeText(preview)}>Copy</button>
              </div>
            </div>}
          </> : <p>Create a global or Project prompt template.</p>}
        </main>
      </div>
      {library?.diagnostics.map(item => <p class="settings-inline-error" key={item.path}>{item.path}: {item.error}</p>)}
      {note && <footer aria-live="polite">{note}</footer>}
    </section>
    {confirmClose && <section class="modal prompt-close-confirm" role="alertdialog" aria-modal="true" onMouseDown={event => event.stopPropagation()}>
      <h3>Unsaved prompt</h3>
      <p>Save or discard the pending template changes.</p>
      <button class="primary" onClick={() => void editor.save().then(saved => saved && onClose())}>Save</button>
      <button onClick={() => { setConfirmClose(false); onClose() }}>Discard</button>
      <button onClick={() => setConfirmClose(false)}>Keep editing</button>
    </section>}
  </div>
}
