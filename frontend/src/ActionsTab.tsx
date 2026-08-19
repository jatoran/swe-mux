import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { ComponentChildren } from 'preact'
import { allRailBackends, isBuiltinRailId, railPayload, resolveRailRows, type RailBackend, type RailItem } from './commandRail'
import { pinPrompt, pinSkill, pinnedPromptItem, pinnedSkillItem, removeScopedRailItem, resolveRail } from './railScope'
import { activatePromptRailItem } from './promptRail'
import { currentProfile, currentRailBlob, loadRailConfig, saveRailBlob } from './deviceSettings'
import { api } from './api'
import {
  filterSkills, groupSkills, inventoryNote, skillLabel, skillTitle,
  type AgentSkill, type SkillInventory,
} from './skills'
import type { Session } from './types'
import { harnessDisplayName, isAgentBackend } from './harnessRegistry'
import { PromptsTab, type PromptsTabProps } from './PromptsTab'
import { ClipboardTab } from './ClipboardPanel'
import { drawerSectionTarget } from './drawerSegments'
import type { PromptTemplate } from './promptTemplates'
import { sessionDisplayName } from './sessionNames'

// The Actions drawer combines four catalogs that can act on the focused session:
// the configured Drawer half of the action layout, the live skill inventory,
// reusable prompt templates, and clipboard history. They share a destination, not an
// identity, so each remains an independently collapsible section.
//
// Clipboard was its own tab until the drawer consolidation. It belongs here by verb —
// every section on this tab ends in text reaching the focused agent, and Clipboard used
// the same `onInsert`/`onDone` contract before it moved. It is a *section* rather than a
// segment for the one reason that decides between the two: sections are co-visible, so
// folding in the surface people reach for fastest cost no extra click. A segment would
// have made "insert the thing I just copied" a three-step navigation.
//
// The strip under a terminal holds what you hammer (Esc, Enter, arrows, ^C); it is
// horizontally scarce, which is why several built-ins used to ship switched off.
// Everything else — extra keys, skills, slash commands, literal text snippets —
// lives here, where room is not the constraint and items can carry full labels.
//
// This is the `panel` surface of the rail layout, and like the strip it is
// arranged per device: the rows below come from the desktop or mobile layout,
// whichever this browser is. Rows are the user's own grouping; within each one
// the terminal keys still split into their own dense grid, because a 44px key and
// a labelled command want different cell sizes.
//
// Below the configured items sits the *discovered* half: the skills the focused
// session's CLI can actually see, read off disk by the daemon from the same
// directories Claude and Codex read. Those are not rail items and are never
// configured here — the list is a window onto the CLI's own state, so it is
// grouped by where each skill comes from and refetched rather than stored.
//
// This tab is session-scoped but renders outside the terminal pane, so it cannot
// touch xterm directly. Every activation goes over the same `mux:terminal-action`
// bus the pane already listens on, which keeps one owner for terminal writes.

type Props = Pick<PromptsTabProps, 'project' | 'backend' | 'onInsert' | 'onManage' | 'sessions' | 'onSend' | 'preselect'> & {
  session: Session | null
  onDone: () => void
  onConfigureActions: () => void
  /** Clipboard's own insert path. Distinct from `onInsert` (prompt templates are
   *  terminals-only) because a copied line may legitimately land in the note the drawer
   *  is hosting, and the host needs to know which happened. */
  onClipboardInsert: (text: string) => 'terminal' | 'editor' | 'none'
  onClipboardDone: () => void
  onOpenSettings: (section: string) => void
  /** One-shot arrival from a palette entry, voice phrase, or Action button that named a
   *  section. Expands it; the host does the scrolling and the flash. */
  reveal?: { section: string; token: number }
}

/** The tab's sections, in draw order. Mirrored by the `actions` rows in `drawerSegments.ts`. */
const ACTION_SECTION_IDS = ['quick', 'skills', 'prompts', 'clipboard'] as const
type ActionSectionId = typeof ACTION_SECTION_IDS[number]
const ACTION_SECTION_KEY = 'mux.actions.sections.v1'

function initialSectionState(): Record<ActionSectionId, boolean> {
  let stored: Record<string, unknown> = {}
  try { stored = JSON.parse(localStorage.getItem(ACTION_SECTION_KEY) || '{}') as Record<string, unknown> }
  catch { stored = {} }
  return Object.fromEntries(ACTION_SECTION_IDS.map(id => [id, stored[id] !== false])) as Record<ActionSectionId, boolean>
}

function ActionSection({ id, title, detail, expanded, onExpanded, action, children }: {
  id: ActionSectionId
  title: string
  detail?: string
  expanded: boolean
  onExpanded: (id: ActionSectionId, expanded: boolean) => void
  action?: { label: string; title: string; run: () => void }
  children: ComponentChildren
}) {
  const bodyId = `actions-section-${id}`
  // `data-setting` on the section itself rather than on its body: the body is unmounted
  // while collapsed, and the reveal has to have something to scroll to and flash even
  // in the frame before the expansion lands.
  return <section
    class={`actions-section actions-section-${id}${expanded ? ' expanded' : ''}`}
    data-setting={drawerSectionTarget('actions', id)}
  >
    <header class="actions-section-header">
      <button type="button" class="actions-section-toggle" aria-expanded={expanded} aria-controls={bodyId} onClick={() => onExpanded(id, !expanded)}>
        <span aria-hidden="true">{expanded ? '▾' : '▸'}</span>
        <strong>{title}</strong>
        {detail && <small>{detail}</small>}
      </button>
      {action && <button type="button" class="actions-section-action" title={action.title} onClick={action.run}>{action.label}</button>}
    </header>
    {expanded && <div id={bodyId} class="actions-section-body">{children}</div>}
  </section>
}

function dispatch(sessionId: string, action: string, detail: Record<string, unknown> = {}) {
  window.dispatchEvent(new CustomEvent('mux:terminal-action', { detail: { sessionId, action, ...detail } }))
}

/** Built-in action items the drawer can run; the rest are injection types. */
const ACTION_LABELS: Record<string, string> = {
  attach: 'Attach',
  paste: 'Paste',
  copyReply: 'Copy reply',
  copyResume: 'Copy resume',
  branch: 'Branch',
  relaunch: 'Relaunch',
  toggleKeyboard: 'Keyboard',
  clipboardHistory: 'Clipboard',
  endSession: 'End session',
}

export function ActionsTab({ session, onDone, onConfigureActions, project, backend: promptBackend, onInsert, onManage, sessions, onSend, preselect, onClipboardInsert, onClipboardDone, onOpenSettings, reveal }: Props) {
  const [note, setNote] = useState('')
  // End session is the one item here that arms a confirm rather than acting, so this
  // tab mirrors the workspace's armed id (broadcast by App) to label the second click.
  const [killArmed, setKillArmed] = useState(false)
  const [inventory, setInventory] = useState<SkillInventory | null>(null)
  const [skillsError, setSkillsError] = useState('')
  const [query, setQuery] = useState('')
  const [sections, setSections] = useState<Record<ActionSectionId, boolean>>(initialSectionState)
  const backend = (session?.backend || 'shell') as RailBackend
  const isAgent = isAgentBackend(backend)
  // Bumped when any surface edits the Action config, so Quick actions and the
  // pin toggles below re-read it without a remount.
  const [railRev, setRailRev] = useState(0)
  useEffect(() => {
    const on = () => setRailRev(value => value + 1)
    window.addEventListener('mux:settings-changed', on)
    return () => window.removeEventListener('mux:settings-changed', on)
  }, [])
  const rows = useMemo(
    () => resolveRailRows(loadRailConfig(session?.project_id), 'panel', { device: currentProfile(), backend }),
    [session?.project_id, backend, railRev],
  )
  // Effective catalog for the pin toggles: pinning creates a placed Action
  // button in one tap (`railScope.ts`), scoped to the project when the skill or
  // template itself is project-scoped.
  const railResolved = useMemo(() => resolveRail(currentRailBlob(), session?.project_id), [session?.project_id, railRev])
  useEffect(() => {
    const on = (event: Event) => setKillArmed((event as CustomEvent<string | null>).detail === session?.id)
    window.addEventListener('mux:kill-armed', on)
    return () => window.removeEventListener('mux:kill-armed', on)
  }, [session?.id])

  // A generation guard rather than a cancel flag: switching the focused session
  // fires a second fetch while the first is still in flight, and the newest must
  // win regardless of which lands first.
  const generation = useRef(0)
  const loadSkills = async (sessionId: string, refresh = false) => {
    const mine = ++generation.current
    try {
      const result = await api<SkillInventory>(
        'GET',
        `/api/sessions/${encodeURIComponent(sessionId)}/skills${refresh ? '?refresh=1' : ''}`,
        undefined,
        { timeoutMs: 10_000 },
      )
      if (mine !== generation.current) return
      setInventory(result); setSkillsError('')
    } catch (cause) {
      if (mine !== generation.current) return
      setInventory(null); setSkillsError(cause instanceof Error ? cause.message : String(cause))
    }
  }
  // The session's live cwd decides which repo skills apply, so this refetches on a
  // cwd change too, not only on a different session.
  useEffect(() => {
    generation.current++
    setInventory(null); setSkillsError(''); setQuery('')
    if (session && isAgent) void loadSkills(session.id)
  }, [session?.id, isAgent, session?.runtime_cwd, session?.run_cwd])
  useEffect(() => {
    if (!preselect?.key) return
    setSections(current => current.prompts ? current : { ...current, prompts: true })
  }, [preselect])
  // Arriving at a named section expands it if it was collapsed. Expanding is all this does;
  // the drawer host owns the scroll and the flash (`settingReveal.ts`), so a section reached
  // from the palette behaves exactly like a setting reached from a deep link.
  const revealSection = reveal?.section
  const revealToken = reveal?.token
  const [clipboardAutoFocus, setClipboardAutoFocus] = useState(0)
  useEffect(() => {
    if (!revealSection) return
    const id = ACTION_SECTION_IDS.find(item => item === revealSection)
    if (!id) return
    setSections(current => current[id] ? current : { ...current, [id]: true })
    // Only the clipboard filter is autofocused, and only on a deliberate arrival: it is the
    // one section whose first action is typing. Doing it on every render of the tab would
    // steal focus from the terminal the drawer opened beside.
    setClipboardAutoFocus(id === 'clipboard' ? (revealToken ?? 0) : 0)
  }, [revealToken, revealSection])

  const setSectionExpanded = (id: ActionSectionId, expanded: boolean) => {
    setSections(current => {
      const next = { ...current, [id]: expanded }
      try { localStorage.setItem(ACTION_SECTION_KEY, JSON.stringify(next)) } catch { /* device preference is best effort */ }
      return next
    })
  }

  const run = (item: RailItem) => {
    // Prompt templates are fetched from the library at click time, so this one path
    // is asynchronous: it closes the drawer only once the insert (or the hand-off to
    // the Prompt templates section for variable filling) has actually happened.
    if (!session) return
    if (item.type === 'prompt') {
      void activatePromptRailItem(item, { sessionId: session.id, projectId: session.project_id })
        .then(result => {
          if (result.status === 'error') setNote(result.message)
          else if (result.status === 'inserted') onDone()
        })
      return
    }
    if (item.type === 'key') dispatch(session.id, 'sendKey', { text: item.bytes || '' })
    else if (item.type === 'action') {
      // `clipboardHistory` now names the Clipboard *section of this tab*, so a button
      // for it here would scroll the surface it is already on. It stays filtered out of
      // the grid below for that reason. Outside the drawer it is still a real action:
      // the terminal strip's Clip key runs `clipboard.open`, which opens this tab and
      // reveals that section.
      dispatch(session.id, item.action || '', {})
      // End session only *arms* a confirm on the first click; closing the drawer here
      // would leave nowhere to make the second one before the window lapses.
      if (item.action === 'endSession' && !killArmed) return
    } else {
      const payload = railPayload(item, backend)
      if (!payload) { setNote(`${item.label} has no payload configured.`); return }
      dispatch(session.id, 'insertText', { text: payload, submit: !!item.submit })
    }
    onDone()
  }

  const insertSkill = (skill: AgentSkill) => {
    if (!session) return
    // Inserted, never submitted: a skill invoked bare runs with no context, and the
    // point of typing it into a live composer is to say what it should act on.
    dispatch(session.id, 'insertText', { text: skill.invocation, submit: false })
    onDone()
  }

  // One-tap pinning: a discovered skill (or a prompt template, below) becomes a
  // placed Quick-actions button on both devices without opening the editor. A
  // project-scoped source pins to the project; everything else pins globally.
  const togglePinSkill = (skill: AgentSkill, pinned: RailItem | null) => {
    const blob = currentRailBlob()
    if (pinned) {
      if (isBuiltinRailId(pinned.id)) { setNote(`“${pinned.label}” is a built-in button — manage it from Configure Actions.`); return }
      void saveRailBlob(removeScopedRailItem(blob, session?.project_id, pinned.id))
      return
    }
    void saveRailBlob(pinSkill(blob, session?.project_id, {
      name: skill.name,
      label: skillLabel(skill),
      kind: skill.kind,
      backend,
      projectScoped: skill.scope === 'project',
    }))
  }

  const promptPin = {
    isPinned: (template: PromptTemplate) => !!pinnedPromptItem(railResolved.config, template.key),
    toggle: (template: PromptTemplate) => {
      const blob = currentRailBlob()
      const existing = pinnedPromptItem(railResolved.config, template.key)
      if (existing) {
        void saveRailBlob(removeScopedRailItem(blob, session?.project_id, existing.id))
        return
      }
      void saveRailBlob(pinPrompt(blob, session?.project_id, {
        key: template.key,
        id: template.id,
        title: template.title,
        backends: template.backends,
        allBackends: allRailBackends(),
        projectScoped: template.scope === 'project',
      }))
    },
  }

  // These two built-ins open this drawer, so running either from inside the drawer
  // would be a no-op; they are filtered out of every row entirely.
  const visibleRows = session ? rows
    .map(row => ({
      ...row,
      entries: row.entries.filter(entry => !['clipboardHistory', 'openActions'].includes(entry.item.action || '') && (entry.item.action !== 'attach' || isAgent)),
    }))
    .filter(row => row.entries.length) : []
  const anyVisible = visibleRows.some(row => row.entries.length)
  const actionDisabled = (item: RailItem) => item.action === 'attach' && (session?.state === 'exited' || session?.state === 'crashed')
  const label = (item: RailItem) =>
    item.action === 'endSession' && killArmed ? 'Confirm ✓'
      : item.type === 'action' ? ACTION_LABELS[item.action || ''] || item.label : item.label
  const matched = inventory ? filterSkills(inventory.skills, query) : []
  const groups = groupSkills(matched)
  const disclosure = inventoryNote(inventory)

  return <div class="actions-tab">
    <p class="drawer-status">{session ? `${sessionDisplayName(session) || session.id} · ${backend}` : 'no terminal focused'}</p>
    <ActionSection
      id="quick"
      title="Quick actions"
      detail="configured Drawer layout"
      expanded={sections.quick}
      onExpanded={setSectionExpanded}
      action={{ label: 'Configure', title: 'Configure Desktop and Mobile Action rail and Drawer layouts', run: onConfigureActions }}
    >
    {!session && <p class="drawer-empty">Quick actions target a terminal. Focus one to use this device’s configured Drawer layout.</p>}
    {session && visibleRows.map(row => {
      const keys = row.entries.filter(entry => entry.item.type === 'key')
      const rest = row.entries.filter(entry => entry.item.type !== 'key')
      return <section class="drawer-command-row" key={row.id}>
        {row.label && <h4 class="drawer-command-row-label">{row.label}</h4>}
        {rest.length > 0 && <div class="drawer-grid" role="group" aria-label={row.label ? `${row.label} actions` : 'Session actions'}>
          {rest.map(({ item, key }) => <button key={key} class={item.action === 'endSession' && killArmed ? 'confirming' : undefined} disabled={actionDisabled(item)} title={actionDisabled(item)?'Files cannot be attached to an ended session':item.title || (item.type === 'prompt' ? 'Insert this prompt template into the composer' : railPayload(item, backend)) || item.label} onClick={() => run(item)}>
            <span>{label(item)}</span>
            {item.type !== 'action' && <small>{item.type === 'skill' ? 'skill' : item.type === 'slash' ? 'command' : item.type === 'prompt' ? 'prompt' : 'text'}{item.type !== 'prompt' && item.submit ? ' · sends' : ''}</small>}
          </button>)}
        </div>}
        {keys.length > 0 && <div class="drawer-grid keys" role="group" aria-label={row.label ? `${row.label} keys` : 'Terminal keys'}>
          {keys.map(({ item, key }) => <button key={key} title={item.title || item.label} onClick={() => run(item)}><span>{item.label}</span></button>)}
        </div>}
      </section>
    })}
    {session && !anyVisible && <p class="drawer-empty">Nothing is assigned to this device’s Drawer layout. Open Configure Actions to add some.</p>}
    {note && <p class="clipboard-note" aria-live="polite">{note}</p>}
    </ActionSection>
    <ActionSection
      id="skills"
      title="Skills"
      detail={session && isAgent && inventory ? `${inventory.skills.length} discovered` : undefined}
      expanded={sections.skills}
      onExpanded={setSectionExpanded}
    >
    {!session && <p class="drawer-empty">Focus an agent session to read its skill inventory.</p>}
    {session && !isAgent && <p class="drawer-empty">Shell sessions do not expose agent skills.</p>}
    {session && isAgent && <section class="drawer-skills">
      <header>
        <h4>{harnessDisplayName(backend)} skills</h4>
        <span>{inventory ? `${matched.length}${query ? ` / ${inventory.skills.length}` : ''}` : ''}</span>
        <button title="Rescan the skill directories now" onClick={() => { if (session) void loadSkills(session.id, true) }}>Rescan</button>
      </header>
      {inventory && inventory.skills.length > 8 && <input
        type="search"
        placeholder="Filter skills"
        aria-label="Filter skills"
        value={query}
        onInput={event => setQuery((event.target as HTMLInputElement).value)}
      />}
      {/* The list is the tab's one unbounded region, so it owns the scroll — the
          header and filter stay put the way every other drawer tab's do. */}
      <div class="drawer-skill-list">
        {!inventory && !skillsError && <p class="drawer-empty">Reading skill directories…</p>}
        {skillsError && <p class="drawer-empty">Skills could not be read: {skillsError}</p>}
        {inventory && !inventory.skills.length && <p class="drawer-empty">No skills on disk for this session. Scanned {inventory.roots.length} directories under {inventory.cwd}.</p>}
        {inventory && !!inventory.skills.length && !matched.length && <p class="drawer-empty">No skill matches “{query}”.</p>}
        {groups.map(group => <div key={group.scope} class="drawer-skill-group">
          <h5>{group.label}</h5>
          {group.skills.map(skill => {
            const pinned = pinnedSkillItem(railResolved.config, skill.name, backend)
            return <div class="drawer-skill-row" key={skill.path}>
              <button
                class={skill.shadowed_by ? 'shadowed' : undefined}
                title={skillTitle(skill)}
                onClick={() => insertSkill(skill)}
              >
                <span>
                  <code>{skill.invocation}</code>
                  {skillLabel(skill) !== skill.name && <em>{skillLabel(skill)}</em>}
                  {skill.added_after_start && <b class="skill-flag warn" title="Added after this agent loaded">new</b>}
                  {!skill.implicit && <b class="skill-flag" title="Explicit-only: the agent never invokes this on its own">explicit</b>}
                </span>
                <small>{skill.short_description || skill.description || skill.origin}</small>
              </button>
              <button
                class={`skill-pin${pinned ? ' on' : ''}`}
                aria-pressed={!!pinned}
                title={pinned
                  ? `Remove the “${pinned.label}” button from your Actions`
                  : `Add a button for this ${skill.kind === 'command' ? 'command' : 'skill'} to Quick actions on both devices${skill.scope === 'project' ? ' — this project only' : ''}`}
                onClick={() => togglePinSkill(skill, pinned)}
              >{pinned ? 'Pinned' : 'Pin'}</button>
            </div>
          })}
        </div>)}
        {disclosure && <p class="drawer-skill-note">{disclosure}</p>}
      </div>
    </section>}
    </ActionSection>
    <ActionSection
      id="prompts"
      title="Prompt templates"
      detail="saved reusable messages"
      expanded={sections.prompts}
      onExpanded={setSectionExpanded}
      action={{ label: 'Manage', title: 'Open the full prompt template editor', run: onManage }}
    >
      <PromptsTab
        project={project}
        backend={promptBackend}
        onInsert={onInsert}
        onDone={onDone}
        onManage={onManage}
        showManage={false}
        sessions={sessions}
        onSend={onSend}
        preselect={preselect}
        pin={promptPin}
      />
    </ActionSection>
    {/* Last, and the only section that is not a *catalog*: the others are things you keep,
        this is things that happened. Its actions are the same verb as everything above it,
        which is why it lives on this tab at all. */}
    <ActionSection
      id="clipboard"
      title="Clipboard"
      detail="recent copies"
      expanded={sections.clipboard}
      onExpanded={setSectionExpanded}
      action={{ label: 'Settings', title: 'Clipboard history capture and retention settings', run: () => onOpenSettings('Input') }}
    >
      <ClipboardTab
        onInsert={onClipboardInsert}
        onDone={onClipboardDone}
        onOpenSettings={() => onOpenSettings('Input')}
        autoFocusToken={clipboardAutoFocus}
      />
    </ActionSection>
  </div>
}
