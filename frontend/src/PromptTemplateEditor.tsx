import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { Dropdown } from './Dropdown'
import { projectDropdownOptions } from './projectOptions'
import { allBackendNames } from './harnessRegistry'
import { notifyPromptLibraryChanged } from './promptLibraryEvents'
import { promptTemplateVariables, type PromptTemplate } from './promptTemplates'
import type { Project, ProjectBackend } from './types'

// One definition of "what a prompt template is to edit", shared by both hosts.
//
// Authoring used to live only in the full-screen library, on the argument that a
// 380px drawer column is the wrong place to write a multi-variable template. That
// was the wrong seam: the frequent edit is a one-line wording fix on a template
// you are looking at right now, and sending that through a modal is three surfaces
// of ceremony for a typo. What the drawer genuinely cannot do is the *wide* view —
// comparing templates, or reaching a template whose Project is not focused — so
// that is what the library keeps.
//
// Both hosts drive the same draft state and the same write calls; only the
// arrangement differs, so there is no second copy of the form to keep in step.
//
// Saving stays explicit. The revision contract means a save can be refused
// because the file changed underneath, and an autosaving field would have nowhere
// to report that without discarding what was typed.

export type PromptDraft = {
  scope: 'global' | 'project'
  projectId: string | null
  title: string
  body: string
  tags: string
  backends: ProjectBackend[]
}

export type PromptDraftState = {
  draft: PromptDraft
  set: (patch: Partial<PromptDraft>) => void
  /** The template being edited, or null while creating one. */
  template: PromptTemplate | null
  dirty: boolean
  busy: boolean
  message: string
  setMessage: (message: string) => void
  /** Placeholders in the *draft* body, so a field appears as it is typed. */
  variables: string[]
  save: () => Promise<PromptTemplate | null>
  remove: () => Promise<boolean>
  revert: () => void
}

export type PromptOwner = { id: string; name: string }

const draftOf = (template: PromptTemplate | null, project: Project | undefined, owners: PromptOwner[]): PromptDraft => {
  if (template) return {
    scope: template.scope,
    projectId: template.project_id || (template.scope === 'project' ? project?.id || null : null),
    title: template.title,
    body: template.body,
    tags: template.tags.join(', '),
    backends: [...template.backends],
  }
  // A new template belongs to the focused Project when that Project accepts one;
  // `owners` is empty when its `prompt_library_scope` excludes Project templates,
  // and offering a scope the daemon will refuse is worse than defaulting global.
  const owner = owners.find(item => item.id === project?.id) || owners[0] || null
  return {
    scope: owner ? 'project' : 'global',
    projectId: owner?.id || null,
    title: '',
    body: '',
    tags: '',
    backends: allBackendNames(),
  }
}

export type PromptDraftOptions = {
  /** The template to edit; null creates one. */
  template: PromptTemplate | null
  /** Focused Project: the default owner for a new Project-scoped template. */
  project?: Project
  /** Projects that accept Project-scoped templates. Empty means Global only. */
  owners?: PromptOwner[]
  /** Changing this restarts the draft even when `template` has not changed —
   *  what "New" needs, since two consecutive creates are both `null`. */
  resetKey?: string | number
  /** Mirror the unsaved draft under this key for the tab's lifetime. The drawer
   *  needs it: it is dismissed by Escape, by a back gesture, and by a tap outside,
   *  none of which can raise a confirmation the way a modal's close button can, so
   *  without a stash an in-progress template dies to a stray keypress. A stash is
   *  dropped as soon as it stops matching the revision it was taken from. */
  persistKey?: string
  onSaved?: (item: PromptTemplate) => void
  onDeleted?: () => void
}

/** Draft state plus the write calls.
 *  `onSaved`/`onDeleted` fire only after the daemon has accepted the write. */
export function usePromptDraft({ template, project, owners = [], resetKey, persistKey, onSaved, onDeleted }: PromptDraftOptions): PromptDraftState {
  const handlers = { onSaved, onDeleted }
  const ownerKey = owners.map(item => item.id).join(' ')
  const initial = useMemo(
    () => draftOf(template, project, owners),
    [template?.key, template?.project_id, template?.revision, project?.id, ownerKey, resetKey],
  )
  const stashKey = persistKey ? `mux.prompt-draft.${persistKey}` : null
  const stashRevision = template?.revision || ''
  const dropStash = () => { if (stashKey) try { sessionStorage.removeItem(stashKey) } catch { /* best effort */ } }
  const restored = (base: PromptDraft): PromptDraft => {
    if (!stashKey) return base
    try {
      const raw = sessionStorage.getItem(stashKey)
      if (!raw) return base
      const stash = JSON.parse(raw) as { revision?: string; draft?: PromptDraft }
      // A stash taken from a revision that is no longer on disk would silently
      // resurrect edits over someone else's save, which is what the revision
      // check on the write exists to prevent. Drop it instead.
      if (stash.revision !== stashRevision || !stash.draft) { dropStash(); return base }
      return { ...base, ...stash.draft }
    } catch { return base }
  }
  const [draft, setDraft] = useState<PromptDraft>(() => restored(initial))
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  // Handlers are captured per render by the host; a ref keeps `save` stable
  // without making every keystroke rebuild it.
  const latest = useRef(handlers)
  latest.current = handlers

  // A different template (or a new revision of the same one, after a save
  // elsewhere) replaces the draft; edits in progress on *this* one do not.
  useEffect(() => setDraft(restored(initial)), [initial, stashKey])

  const dirty = JSON.stringify(draft) !== JSON.stringify(initial)
  const variables = useMemo(() => promptTemplateVariables(draft.body), [draft.body])

  useEffect(() => {
    if (!stashKey) return
    if (!dirty) { dropStash(); return }
    try { sessionStorage.setItem(stashKey, JSON.stringify({ revision: stashRevision, draft })) } catch { /* best effort */ }
  }, [stashKey, stashRevision, dirty, draft])

  const save = async (): Promise<PromptTemplate | null> => {
    if (busy) return null
    // The owning Project is the template's own, not whichever is focused: the
    // management view edits templates belonging to Projects it is not inside.
    const projectId = draft.scope === 'project' ? draft.projectId || project?.id || null : project?.id || null
    if (draft.scope === 'project' && !projectId) {
      setMessage('Choose a Project for a Project-scoped template.')
      return null
    }
    const body = {
      project_id: projectId,
      scope: draft.scope,
      title: draft.title,
      body: draft.body,
      tags: draft.tags.split(',').map(tag => tag.trim()).filter(Boolean),
      backends: draft.backends,
      revision: template?.revision,
    }
    setBusy(true)
    try {
      const saved = template
        ? await api<PromptTemplate>('PUT', `/api/prompts/${template.scope}/${template.id}`, body)
        : await api<PromptTemplate>('POST', '/api/prompts', body)
      notifyPromptLibraryChanged()
      dropStash()
      setMessage('Saved.')
      latest.current.onSaved?.(saved)
      return saved
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : String(cause))
      return null
    } finally {
      setBusy(false)
    }
  }

  const remove = async (): Promise<boolean> => {
    if (!template || busy) return false
    setBusy(true)
    try {
      await api('DELETE', `/api/prompts/${template.scope}/${template.id}`, {
        project_id: template.project_id || project?.id,
        revision: template.revision,
      })
      notifyPromptLibraryChanged()
      dropStash()
      latest.current.onDeleted?.()
      return true
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : String(cause))
      return false
    } finally {
      setBusy(false)
    }
  }

  return {
    draft,
    set: patch => { setDraft(current => ({ ...current, ...patch })); setMessage('') },
    template,
    dirty,
    busy,
    message,
    setMessage,
    variables,
    save,
    remove,
    revert: () => { dropStash(); setDraft(initial); setMessage('') },
  }
}

/** The form itself. `projects` offers an owner choice while creating a
 *  Project-scoped template from the widened management view; an existing
 *  template shows its owner as text, because moving one between Projects is a
 *  file move the revision contract does not cover. */
export function PromptDraftFields({ state, owners = [], compact }: {
  state: PromptDraftState
  owners?: PromptOwner[]
  compact?: boolean
}) {
  const { draft, set, template } = state
  const owner = owners.find(item => item.id === draft.projectId)
  return <div class={`prompt-draft-fields${compact ? ' compact' : ''}`}>
    <label>Title
      <input value={draft.title} placeholder="Name this template" onInput={event => set({ title: event.currentTarget.value })} />
    </label>
    <div class="prompt-draft-row">
      <label>Scope
        <Dropdown value={draft.scope} disabled={Boolean(template)} onChange={value => {
          const scope = value as PromptDraft['scope']
          set({ scope, projectId: scope === 'project' ? draft.projectId || owners[0]?.id || null : null })
        }} options={[
          { value: 'global', label: 'Global' },
          ...(owners.length > 0 ? [{ value: 'project', label: 'Project' }] : []),
        ]}/>
      </label>
      {draft.scope === 'project' && <label>Project
        {template
          ? <input value={owner?.name || draft.projectId || 'unknown'} readOnly />
          : <Dropdown value={draft.projectId || ''} disabled={owners.length < 2} onChange={value => set({ projectId: value })}
            filter filterPlaceholder="Filter Projects…"
            options={projectDropdownOptions(owners, item => ({ value: item.id, label: item.name }))}/>}
      </label>}
    </div>
    <label>Tags
      <input value={draft.tags} placeholder="review, git, planning" onInput={event => set({ tags: event.currentTarget.value })} />
    </label>
    <fieldset>
      <legend>Compatible backends</legend>
      {allBackendNames().map(value => <label class="check" key={value}>
        <span>{value}</span>
        <input
          type="checkbox"
          checked={draft.backends.includes(value)}
          onChange={event => set({
            backends: event.currentTarget.checked
              ? [...draft.backends, value]
              : draft.backends.filter(item => item !== value),
          })}
        />
      </label>)}
    </fieldset>
    <label>Template body
      <textarea
        rows={compact ? 8 : 14}
        value={draft.body}
        placeholder="Use {{variable_name}} placeholders."
        onInput={event => set({ body: event.currentTarget.value })}
      />
    </label>
    {state.variables.length > 0 && <p class="prompt-draft-note">Fields: {state.variables.join(', ')}</p>}
  </div>
}

/** Save / Revert / Delete. Delete arms a confirm in place rather than opening a
 *  dialog, so the drawer and the modal need no shared overlay. */
export function PromptDraftActions({ state, onCancel, cancelLabel = 'Cancel' }: {
  state: PromptDraftState
  onCancel?: () => void
  cancelLabel?: string
}) {
  const [confirmDelete, setConfirmDelete] = useState(false)
  useEffect(() => setConfirmDelete(false), [state.template?.key])
  return <div class="prompt-actions prompt-draft-actions">
    <button class="primary" disabled={!state.dirty || state.busy} onClick={() => void state.save()}>
      {state.busy ? 'Saving…' : state.template ? 'Save' : 'Create'}
    </button>
    {state.dirty && <button disabled={state.busy} onClick={state.revert}>Revert</button>}
    {onCancel && <button disabled={state.busy} onClick={onCancel}>{cancelLabel}</button>}
    {state.template && !confirmDelete && <button class="danger" disabled={state.busy} onClick={() => setConfirmDelete(true)}>Delete</button>}
    {state.template && confirmDelete && <>
      <button class="danger" disabled={state.busy} onClick={() => void state.remove()}>Confirm delete</button>
      <button disabled={state.busy} onClick={() => setConfirmDelete(false)}>Keep</button>
    </>}
  </div>
}
