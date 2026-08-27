import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { api } from './api'
import { agentTargetName, agentTargets } from './agentTargets'
import { wordReasons } from './deliveryReadiness'
import { clampContextMenuLeft, fitScrollingMenuInViewport } from './menuPosition'
import { subscribeToPromptLibraryChanges } from './promptLibraryEvents'
import { promptTemplateExcerpt, promptTemplateRef, renderPromptTemplate, type PromptTemplate } from './promptTemplates'
import { PromptDraftActions, PromptDraftFields, usePromptDraft } from './PromptTemplateEditor'
import { StateIndicator } from './StateIndicator'
import { useSessionRowConfig } from './sessionRowPrefs'
import type { SendToAgentResult, SendToAgentTarget } from './SendToAgentPicker'
import type { Project, ProjectBackend, Session } from './types'
import { harnessDisplayName, promptDeliveryHarnesses } from './harnessRegistry'

// Prompt templates: browse, insert, and author.
//
// Inserting, which is the frequent action, belongs here next to clipboard history —
// both are "text into the focused session", and both should be one swipe away
// rather than three menu levels deep. Authoring is here for the same reason: the
// common edit is a wording fix on the template you are already looking at, and
// routing that through a modal is three surfaces of ceremony for a typo. The form
// itself is `PromptTemplateEditor`, shared verbatim with the full library, so the
// two hosts differ in arrangement and never in what a template *is*. The library
// keeps the one thing a 380px column cannot do: the wide, cross-Project view.
//
// Two ways out of a row, deliberately different:
//
//  * tap/click  — insert into the focused *terminal*, never submitting. Notes and
//    file editors are excluded as targets: a template is written for an agent, and
//    dropping one into whichever document was last touched edits that document.
//  * right-click / long-press — a target menu: any live agent session in this
//    Project, or a brand new Claude/Codex session. That path does send (the human
//    picked a recipient, which is the intent an Enter would otherwise ask for), and
//    the Enter itself stays toggleable in the menu.

export type PromptsTabProps = {
  project?: Project
  backend?: ProjectBackend
  /** Insert into the focused terminal. 'editor' is never returned here — the caller
   *  routes prompt inserts terminals-only — but the shared signature is kept. */
  onInsert: (text: string) => 'terminal' | 'editor' | 'none'
  onDone: () => void
  onManage: () => void
  /** The Actions section owns the management affordance when embedded there. */
  showManage?: boolean
  /** Every known session; the target menu filters to this Project's live agents. */
  sessions: Session[]
  /** Deliver to a chosen target. Resolves to '' on success, or to a message to show. */
  onSend: (target: SendToAgentTarget, text: string) => Promise<SendToAgentResult>
  /** A command-rail prompt button that needs placeholders filled hands the template
   *  off here. Wrapped in an object so re-firing the same button re-expands it. */
  preselect?: { key: string }
}

type PromptOwner = { id: string; name: string }
/** `projects` names the Projects the daemon actually read a Project library for,
 *  which is the authority on whether this Project accepts one: its
 *  `prompt_library_scope` may be `global`, and offering an owner the daemon will
 *  refuse turns a create into an error the user cannot act on. */
type Library = { items: PromptTemplate[]; projects?: PromptOwner[] }
type TargetMenu = { item: PromptTemplate; x: number; y: number }
/** Open inline editor. `nonce` restarts the draft when "New" is pressed twice,
 *  since both times the template being edited is `null`. */
type InlineEdit = { template: PromptTemplate | null; nonce: number }
/** A target chosen before its template's placeholders were filled: the expanded
 *  field block sends there instead of inserting once the fields are complete. */
type PendingSend = { key: string; target: SendToAgentTarget }

const LONG_PRESS_MS = 550

export function PromptsTab({ project, backend, onInsert, onDone, onManage, showManage = true, sessions, onSend, preselect }: PromptsTabProps) {
  const rowConfig = useSessionRowConfig()
  const [items, setItems] = useState<PromptTemplate[] | null>(null)
  const [owners, setOwners] = useState<PromptOwner[]>([])
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [variables, setVariables] = useState<Record<string, string>>({})
  const [note, setNote] = useState('')
  const [menu, setMenu] = useState<TargetMenu | null>(null)
  const [pending, setPending] = useState<PendingSend | null>(null)
  const [submit, setSubmit] = useState(true)
  const [busy, setBusy] = useState(false)
  const [edit, setEdit] = useState<InlineEdit | null>(null)
  const editNonce = useRef(0)
  const menuPanel = useRef<HTMLDivElement>(null)
  const longPress = useRef<number | null>(null)
  const pressOrigin = useRef<{ x: number; y: number } | null>(null)
  // Which device opened the menu: a mouse right-click is followed by no click at all,
  // so suppressing one there would swallow the user's next ordinary left-click.
  const touchPress = useRef(false)
  // A touch long press can still end in a click on the row it opened over; without
  // this the menu would open and the row would insert underneath it.
  const suppressClick = useRef(false)
  // Menus open under the finger, so the lift can land on the item now sitting there.
  const menuOpenedAt = useRef(0)

  useEffect(() => {
    const load = () => {
      const scope = project ? `?project_id=${encodeURIComponent(project.id)}` : ''
      return api<Library>('GET', `/api/prompts${scope}`)
        .then(library => { setItems(library.items); setOwners(library.projects || []); setNote('') })
        .catch(cause => setNote(cause instanceof Error ? cause.message : String(cause)))
    }
    void load()
    return subscribeToPromptLibraryChanges(() => { void load() })
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
    setPending(null)
    setVariables(Object.fromEntries(target.variables.map(name => [name, ''])))
  }, [preselect, items])

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    return (items || []).filter(item =>
      (!backend || item.backends.includes(backend)) &&
      (!needle || `${item.title} ${item.tags.join(' ')} ${item.body}`.toLocaleLowerCase().includes(needle)))
  }, [items, query, backend])

  // The menu only offers this Project's live Claude/Codex sessions — the same rule as
  // send-to-agent, and for the same reason: a shell would run the template as commands.
  const targets = useMemo(() => project ? agentTargets(sessions, project.id) : [], [sessions, project?.id])

  const active = filtered.find(item => item.key === selected) || null
  // A template with no placeholders is a one-tap insert; one with placeholders
  // expands its fields in place rather than opening a second surface.
  const missing = active?.variables.filter(name => !variables[name]?.trim()) || []

  useEffect(() => () => { if (longPress.current !== null) window.clearTimeout(longPress.current) }, [])

  // Dismissal is on pointerdown, not mousedown: a touch long-press synthesises a
  // mousedown when the finger lifts, which would close the menu it just opened.
  useEffect(() => {
    if (!menu) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const frame = requestAnimationFrame(() => menuPanel.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus())
    const dismiss = (event: Event) => {
      const target = event.target
      if (target instanceof Element && target.closest('.prompt-target-menu')) return
      setMenu(null)
    }
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); setMenu(null); return }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
      const buttons = [...menuPanel.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || []]
      if (!buttons.length) return
      event.preventDefault()
      const current = buttons.indexOf(document.activeElement as HTMLButtonElement)
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
        : (Math.max(current, 0) + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
      buttons[next].focus()
    }
    document.addEventListener('pointerdown', dismiss)
    window.addEventListener('blur', dismiss)
    window.addEventListener('keydown', key, true)
    return () => {
      cancelAnimationFrame(frame)
      document.removeEventListener('pointerdown', dismiss)
      window.removeEventListener('blur', dismiss)
      window.removeEventListener('keydown', key, true)
      previous?.focus()
    }
  }, [menu])

  const choose = (item: PromptTemplate) => {
    setPending(null)
    if (!item.variables.length) { void insert(item, item.body); return }
    setSelected(current => current === item.key ? null : item.key)
    setVariables(Object.fromEntries(item.variables.map(name => [name, ''])))
  }

  const recordUse = (item: PromptTemplate) =>
    api('POST', `/api/prompts/${item.scope}/${item.id}/use`, { project_id: project?.id }).catch(() => {})

  const insert = async (item: PromptTemplate, text: string) => {
    if (onInsert(text) === 'none') { setNote('Focus an agent session first, then insert.'); return }
    await recordUse(item)
    onDone()
  }

  const targetLabel = (target: SendToAgentTarget) =>
    target.kind === 'new' ? `new ${harnessDisplayName(target.backend)}` : agentTargetName(target.session)

  const send = async (item: PromptTemplate, text: string, target: SendToAgentTarget) => {
    setBusy(true)
    const result = await onSend(target, text)
    setBusy(false)
    if (result.status === 'error') { setNote(result.error); return }
    if (result.status === 'blocked') {
      // The message is staged (blocked) in the target's queue; the Queue panel and the
      // send dialog own the explicit confirmation flow.
      setNote(`Queued but not delivered: ${wordReasons(result.reasons)}. Confirm from the Queue tab of this panel.`)
      await recordUse(item)
      return
    }
    setNote('')
    await recordUse(item)
    onDone()
  }

  /** Menu entry picked. A template whose placeholders are still empty has nothing
   *  valid to send, so the target is parked and its fields are expanded instead. */
  const chooseTarget = (item: PromptTemplate, target: SendToAgentTarget) => {
    setMenu(null)
    if (!item.variables.length) { void send(item, item.body, target); return }
    const values = item.key === selected ? variables : {}
    const unfilled = item.variables.filter(name => !values[name]?.trim())
    if (!unfilled.length) { void send(item, renderPromptTemplate(item.body, values), target); return }
    if (item.key !== selected) {
      setSelected(item.key)
      setVariables(Object.fromEntries(item.variables.map(name => [name, ''])))
    }
    setPending({ key: item.key, target })
    setNote(`Fill ${unfilled.join(', ')} to send to ${targetLabel(target)}.`)
  }

  const openMenu = (item: PromptTemplate, x: number, y: number) => {
    setNote('')
    menuOpenedAt.current = performance.now()
    setMenu({ item, x, y })
  }

  const cancelLongPress = () => {
    if (longPress.current !== null) window.clearTimeout(longPress.current)
    longPress.current = null
    pressOrigin.current = null
  }

  const beginLongPress = (item: PromptTemplate, event: JSX.TargetedPointerEvent<HTMLElement>) => {
    touchPress.current = event.pointerType === 'touch'
    if (!touchPress.current) return
    cancelLongPress()
    const { clientX, clientY } = event
    pressOrigin.current = { x: clientX, y: clientY }
    longPress.current = window.setTimeout(() => {
      longPress.current = null
      suppressClick.current = true
      navigator.vibrate?.(20)
      openMenu(item, clientX, clientY)
    }, LONG_PRESS_MS)
  }

  /** Finger jitter is not a scroll: only real movement cancels the press, or a list
   *  that is hard to hold still on would never reach the menu. */
  const trackLongPress = (event: JSX.TargetedPointerEvent<HTMLElement>) => {
    const origin = pressOrigin.current
    if (!origin) return
    if (Math.abs(event.clientX - origin.x) > 10 || Math.abs(event.clientY - origin.y) > 10) cancelLongPress()
  }

  const armed = pending && active && pending.key === active.key ? pending : null

  // The inline editor is the same form the full library uses. It replaces the list
  // rather than expanding under a row: the fields need the whole column, and a
  // half-scrolled row above an open form is how the row you meant to edit gets lost.
  const draft = usePromptDraft({
    template: edit?.template || null,
    project,
    owners,
    resetKey: edit ? edit.nonce : 'closed',
    // The drawer has no dismissal it can intercept, so an open draft is mirrored
    // and comes back when the tab does.
    persistKey: edit ? (edit.template ? promptTemplateRef(edit.template) : 'new') : undefined,
    // The write already invalidated every prompt view, and this tab subscribes to
    // that, so the list behind the editor is current by the time it reappears.
    onSaved: () => setEdit(null),
    onDeleted: () => setEdit(null),
  })
  const openEditor = (template: PromptTemplate | null) => {
    setNote('')
    setMenu(null)
    setEdit({ template, nonce: ++editNonce.current })
  }
  const closeEditor = () => {
    if (draft.dirty) { draft.setMessage('Save or revert this template before closing.'); return }
    setEdit(null)
  }

  if (edit) return <div class="prompt-inline-editor">
    <header class="prompt-inline-head">
      <button onClick={closeEditor} title="Back to the template list">‹ Templates</button>
      <strong>{edit.template ? 'Edit template' : 'New template'}</strong>
    </header>
    <PromptDraftFields state={draft} owners={owners} compact />
    <PromptDraftActions state={draft} onCancel={closeEditor} cancelLabel="Close" />
    {draft.message && <p class="clipboard-note" aria-live="polite">{draft.message}</p>}
    <footer class="drawer-actions"><button onClick={onManage}>Open full library…</button></footer>
  </div>

  return <>
    <p class="drawer-status">{project ? project.name : 'global templates'}{backend ? ` · ${backend}` : ''} · tap inserts · long-press to send</p>
    <div class="clipboard-search prompt-drawer-search">
      <input value={query} onInput={event => setQuery(event.currentTarget.value)} placeholder="Filter templates…" aria-label="Filter prompt templates" />
      <button title="Write a new prompt template here" onClick={() => openEditor(null)}>New</button>
    </div>
    <div class="clipboard-entries" role="group" aria-label="Prompt templates">
      {filtered.map(item => <div key={item.key} class={`clipboard-entry ${selected === item.key ? 'active' : ''} has-actions`}>
        <button
          class="clipboard-entry-body prompt-entry-body"
          title={`${item.variables.length ? 'Fill placeholders, then insert' : 'Insert into the focused session'} · right-click or long-press to send to a session`}
          onContextMenu={event => {
            event.preventDefault(); event.stopPropagation()
            cancelLongPress()
            // A mouse right-click is followed by no click, so suppressing one here
            // would swallow the next ordinary left-click instead.
            if (touchPress.current) suppressClick.current = true
            openMenu(item, event.clientX, event.clientY)
          }}
          onPointerDown={event => beginLongPress(item, event)}
          onPointerMove={trackLongPress}
          onPointerUp={cancelLongPress}
          onPointerCancel={cancelLongPress}
          onPointerLeave={cancelLongPress}
          onClick={() => { if (suppressClick.current) { suppressClick.current = false; return } choose(item) }}
        >
          {/* Scope, tags and the field count ride the title's own row as pills. As a
              second `<small>` under the title they inherited the clipboard row's
              `meta` grid area, which the excerpt also claims, and the two drew on
              top of each other. */}
          <span class="prompt-row-head">
            <span class="prompt-row-title">{item.favorite ? '★ ' : ''}{item.title}</span>
            <span class="prompt-row-tags">
              <b class="prompt-pill scope">{item.scope}</b>
              {item.tags.map(tag => <b class="prompt-pill" key={tag}>{tag}</b>)}
              {item.variables.length > 0 && <b class="prompt-pill fields">{item.variables.length} field{item.variables.length === 1 ? '' : 's'}</b>}
            </span>
          </span>
          <small class="prompt-template-excerpt">{promptTemplateExcerpt(item.body)}</small>
        </button>
        <button
          class="prompt-row-edit"
          title={`Edit “${item.title}” here`}
          aria-label={`Edit ${item.title}`}
          onClick={() => openEditor(item)}
        >Edit</button>
      </div>)}
      {active && active.variables.length > 0 && <div class="drawer-fields">
        {active.variables.map(name => <label key={name}>{name}
          <input value={variables[name] || ''} onInput={event => setVariables({ ...variables, [name]: event.currentTarget.value })} />
        </label>)}
        {armed
          ? <button class="primary" disabled={busy || missing.length > 0} title={missing.length ? `Fill: ${missing.join(', ')}` : `Send to ${targetLabel(armed.target)}`} onClick={() => void send(active, renderPromptTemplate(active.body, variables), armed.target)}>
            {busy ? 'Sending…' : `Send to ${targetLabel(armed.target)}`}
          </button>
          : <button class="primary" disabled={missing.length > 0} title={missing.length ? `Fill: ${missing.join(', ')}` : 'Insert without submitting'} onClick={() => void insert(active, renderPromptTemplate(active.body, variables))}>Insert</button>}
        {armed && <button disabled={busy} onClick={() => { setPending(null); setNote('') }}>Insert instead</button>}
      </div>}
      {items && !filtered.length && <p class="drawer-empty">{items.length ? 'No template matches that filter.' : 'No prompt templates yet.'}</p>}
    </div>
    {note && <p class="clipboard-note" aria-live="polite">{note}</p>}
    {showManage && <footer class="drawer-actions"><button onClick={onManage}>Manage templates…</button></footer>}
    {menu && <div
      class="context-menu prompt-target-menu"
      ref={el => { menuPanel.current = el; fitScrollingMenuInViewport(el) }}
      role="menu"
      aria-label={`Send ${menu.item.title} to`}
      style={{ left: clampContextMenuLeft(menu.x, window.innerWidth), top: Math.max(4, menu.y) }}
      // The menu opens under the finger that opened it, so the lift can land on
      // whichever item now sits there. Nothing fires for the first moment.
      onClickCapture={event => { if (performance.now() - menuOpenedAt.current < 250) { event.preventDefault(); event.stopPropagation() } }}
    >
      <div class="context-title"><strong>{menu.item.title}</strong></div>
      <button role="menuitem" onClick={() => { const item = menu.item; setMenu(null); choose(item) }}>
        {menu.item.variables.length ? 'Fill fields, then insert' : 'Insert into focused session'}
      </button>
      <button role="menuitem" onClick={() => openEditor(menu.item)}>Edit template</button>
      <div class="context-subtitle">SEND TO SESSION</div>
      {targets.map(session => <button
        key={session.id}
        role="menuitem"
        title={`${session.backend} · ${session.state}${session.state_detail ? ` · ${session.state_detail}` : ''}`}
        onClick={() => chooseTarget(menu.item, { kind: 'session', session, submit })}
      ><StateIndicator session={session} shape={rowConfig.dotShape} />{agentTargetName(session)}</button>)}
      {!targets.length && <button role="menuitem" disabled>{project ? 'No live agent session here' : 'Select a Project first'}</button>}
      <div class="context-subtitle">NEW SESSION</div>
      {promptDeliveryHarnesses().map(harness=><button role="menuitem" disabled={!project} onClick={() => project && chooseTarget(menu.item, { kind: 'new', backend: harness.name, projectId: project.id })}>New {harness.display_name} session</button>)}
      <div class="context-rule" />
      {/* A new session always submits (its prompt travels on the command line); this
          only governs the live-session writes, so the library's insert-never-send
          contract stays reachable from the menu too. */}
      <button role="menuitem" aria-pressed={submit} onClick={() => setSubmit(value => !value)}>{submit ? '☑' : '☐'} Press Enter after sending</button>
    </div>}
  </>
}
