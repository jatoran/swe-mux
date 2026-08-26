import { useEffect, useRef, useState } from 'preact/hooks'
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

// The Actions drawer combines three catalogs that can act on the focused session:
// the live skill inventory, reusable prompt templates, and clipboard history.
// They share a destination, not an identity, so each is one named view under a persistent
// tab row. Rendering one catalog at a time keeps three potentially long inventories from
// becoming one overwhelming scroller.
//
// The command rail owns placed shortcuts and its permanent drawer popover exposes
// the complete configured row. This tab owns the discovered catalogs instead: the skills the focused
// session's CLI can actually see, read off disk by the daemon from the same
// directories Claude and Codex read. Those are not rail items and are never
// configured here - the list is a window onto the CLI's own state, so it is
// grouped by where each skill comes from and refetched rather than stored.
//
// This tab is session-scoped but renders outside the terminal pane, so it cannot
// touch xterm directly. Every activation goes over the same `mux:terminal-action`
// bus the pane already listens on, which keeps one owner for terminal writes.

type Props = Pick<PromptsTabProps, 'project' | 'backend' | 'onInsert' | 'onManage' | 'sessions' | 'onSend' | 'preselect'> & {
  session: Session | null
  onDone: () => void
  /** Clipboard's own insert path. Distinct from `onInsert` (prompt templates are
   *  terminals-only) because a copied line may legitimately land in the note the drawer
   *  is hosting, and the host needs to know which happened. */
  onClipboardInsert: (text: string) => 'terminal' | 'editor' | 'none'
  onClipboardDone: () => void
  onOpenSettings: (section: string) => void
  /** One-shot arrival from a palette entry, voice phrase, or Action button that named a view. */
  reveal?: { section: string; token: number }
}
const ACTION_VIEWS = ['skills', 'prompts', 'clipboard'] as const
type ActionView = typeof ACTION_VIEWS[number]
const ACTION_VIEW_KEY = 'mux.actions.view.v1'

function initialView(): ActionView {
  try {
    const stored=localStorage.getItem(ACTION_VIEW_KEY)
    return ACTION_VIEWS.find(view=>view===stored)||'skills'
  } catch { return 'skills' }
}

function dispatch(sessionId: string, action: string, detail: Record<string, unknown> = {}) {
  window.dispatchEvent(new CustomEvent('mux:terminal-action', { detail: { sessionId, action, ...detail } }))
}

export function ActionsTab({ session, onDone, project, backend: promptBackend, onInsert, onManage, sessions, onSend, preselect, onClipboardInsert, onClipboardDone, onOpenSettings, reveal }: Props) {
  const [inventory, setInventory] = useState<SkillInventory | null>(null)
  const [skillsError, setSkillsError] = useState('')
  const [query, setQuery] = useState('')
  const [view,setViewState]=useState<ActionView>(initialView)
  const [selectedSkill,setSelectedSkill]=useState('')
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
    setViewState('prompts')
  }, [preselect])
  // Arriving at a named section expands it if it was collapsed. Expanding is all this does;
  // the drawer host owns the scroll and the flash (`settingReveal.ts`), so a section reached
  // from the palette behaves exactly like a setting reached from a deep link.
  const revealSection = reveal?.section
  const revealToken = reveal?.token
  const [clipboardAutoFocus, setClipboardAutoFocus] = useState(0)
  useEffect(() => {
    if (!revealSection) return
    const id = ACTION_VIEWS.find(item => item === revealSection)
    if (!id) return
    setViewState(id)
    // Only the clipboard filter is autofocused, and only on a deliberate arrival: it is the
    // one section whose first action is typing. Doing it on every render of the tab would
    // steal focus from the terminal the drawer opened beside.
    setClipboardAutoFocus(id === 'clipboard' ? (revealToken ?? 0) : 0)
  }, [revealToken, revealSection])

  const setView=(next:ActionView)=>{
    try{localStorage.setItem(ACTION_VIEW_KEY,next)}catch{/* device preference is best effort */}
    setViewState(next)
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

  return <div class="actions-tab">
    <div class="actions-view-tabs" role="tablist" aria-label="Actions catalog">
      <button type="button" role="tab" aria-selected={view==='skills'} class={view==='skills'?'active':''} onClick={()=>setView('skills')}>
        Skills{inventory?` ${inventory.skills.length}`:''}
      </button>
      <button type="button" role="tab" aria-selected={view==='prompts'} class={view==='prompts'?'active':''} onClick={()=>setView('prompts')}>Prompts</button>
      <button type="button" role="tab" aria-selected={view==='clipboard'} class={view==='clipboard'?'active':''} onClick={()=>setView('clipboard')}>Clipboard</button>
    </div>
    {view==='skills'&&<div class="actions-view actions-skills-view" data-setting="drawer.actions.skills">
      {!session&&<p class="drawer-empty">Focus an agent session to read its skill inventory.</p>}
      {session&&!isAgent&&<p class="drawer-empty">Shell sessions do not expose agent skills.</p>}
      {session&&isAgent&&<section class="drawer-skills">
        <header><h4>{harnessDisplayName(backend)} skills</h4><span>{inventory?`${matched.length}${query?` / ${inventory.skills.length}`:''}`:''}</span><button title="Rescan the skill directories now" onClick={()=>{if(session)void loadSkills(session.id,true)}}>Rescan</button></header>
        {inventory&&inventory.skills.length>8&&<input type="search" placeholder="Filter skills" aria-label="Filter skills" value={query} onInput={event=>setQuery((event.target as HTMLInputElement).value)}/>}
        <div class="drawer-skill-list">
          {!inventory&&!skillsError&&<p class="drawer-empty">Reading skill directories…</p>}
          {skillsError&&<p class="drawer-empty">Skills could not be read: {skillsError}</p>}
          {inventory&&!inventory.skills.length&&<p class="drawer-empty">No skills on disk for this session. Scanned {inventory.roots.length} directories under {inventory.cwd}.</p>}
          {inventory&&!!inventory.skills.length&&!matched.length&&<p class="drawer-empty">No skill matches “{query}”.</p>}
          {groups.map(group=><div key={group.scope} class="drawer-skill-group"><h5>{group.label}</h5>
            {group.skills.map(skill=>{
              const selected=selectedSkill===skill.path
              return <div class={`drawer-skill-row compact${selected?' selected':''}`} key={skill.path}>
                <button class={skill.shadowed_by?'shadowed':undefined} title={skillTitle(skill)} aria-expanded={selected} onClick={()=>setSelectedSkill(current=>current===skill.path?'':skill.path)}>
                  <span><code>{skill.invocation}</code>{skillLabel(skill)!==skill.name&&<em>{skillLabel(skill)}</em>}{skill.added_after_start&&<b class="skill-flag warn">new</b>}{!skill.implicit&&<b class="skill-flag">explicit</b>}</span>
                  <small>{skill.short_description||skill.description||skill.origin}</small>
                </button>
                {selected&&<div class="drawer-skill-detail"><p>{skill.description||skill.short_description||'No description provided.'}</p><small>{skill.origin}</small><button class="primary" disabled={!!skill.shadowed_by||skill.added_after_start} onClick={()=>insertSkill(skill)}>Insert {skill.invocation}</button></div>}
              </div>
            })}
          </div>)}
          {disclosure&&<p class="drawer-skill-note">{disclosure}</p>}
        </div>
      </section>}
    </div>}
    {view==='prompts'&&<div class="actions-view" data-setting="drawer.actions.prompts"><PromptsTab project={project} backend={promptBackend} onInsert={onInsert} onDone={onDone} onManage={onManage} sessions={sessions} onSend={onSend} preselect={preselect}/></div>}
    {view==='clipboard'&&<div class="actions-view" data-setting="drawer.actions.clipboard"><ClipboardTab onInsert={onClipboardInsert} onDone={onClipboardDone} onOpenSettings={()=>onOpenSettings('Input')} autoFocusToken={clipboardAutoFocus}/></div>}
  </div>
}
