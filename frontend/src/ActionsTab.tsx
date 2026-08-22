import { useEffect, useRef, useState } from 'preact/hooks'
import type { ComponentChildren } from 'preact'
import type { RailBackend } from './commandRail'
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

// The Actions drawer combines three catalogs that can act on the focused session:
// the live skill inventory, reusable prompt templates, and clipboard history.
// They share a destination, not an
// identity, so each remains an independently collapsible section.
//
// Clipboard was its own tab until the drawer consolidation. It belongs here by verb —
// every section on this tab ends in text reaching the focused agent, and Clipboard used
// the same `onInsert`/`onDone` contract before it moved. It is a *section* rather than a
// segment for the one reason that decides between the two: sections are co-visible, so
// folding in the surface people reach for fastest cost no extra click. A segment would
// have made "insert the thing I just copied" a three-step navigation.
//
// The command rail owns placed shortcuts and its permanent drawer popover exposes
// the complete configured row. This tab owns the discovered catalogs instead: the skills the focused
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
const ACTION_SECTION_IDS = ['skills', 'prompts', 'clipboard'] as const
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

export function ActionsTab({ session, onDone, onConfigureActions, project, backend: promptBackend, onInsert, onManage, sessions, onSend, preselect, onClipboardInsert, onClipboardDone, onOpenSettings, reveal }: Props) {
  const [inventory, setInventory] = useState<SkillInventory | null>(null)
  const [skillsError, setSkillsError] = useState('')
  const [query, setQuery] = useState('')
  const [sections, setSections] = useState<Record<ActionSectionId, boolean>>(initialSectionState)
  const backend = (session?.backend || 'shell') as RailBackend
  const isAgent = isAgentBackend(backend)

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

  const insertSkill = (skill: AgentSkill) => {
    if (!session) return
    // Inserted, never submitted: a skill invoked bare runs with no context, and the
    // point of typing it into a live composer is to say what it should act on.
    dispatch(session.id, 'insertText', { text: skill.invocation, submit: false })
    onDone()
  }

  const matched = inventory ? filterSkills(inventory.skills, query) : []
  const groups = groupSkills(matched)
  const disclosure = inventoryNote(inventory)

  // No session line. The pane heading above this tab already names the focused session
  // and says so in more words ("Session: <name>"), and the harness this tab is resolved
  // against is stated where it decides anything - the Skills section's own header.
  return <div class="actions-tab">
    <div class="actions-tab-toolbar">
      <button type="button" title="Choose command rail actions, order, rows, and button appearance" onClick={onConfigureActions}>Configure command rail</button>
    </div>
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
          {group.skills.map(skill => <div class="drawer-skill-row" key={skill.path}>
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
            </div>
          )}
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
