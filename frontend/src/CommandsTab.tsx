import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { railPayload, resolveRail, type RailBackend, type RailItem } from './commandRail'
import { activatePromptRailItem } from './promptRail'
import { currentProfile, loadRailItems } from './deviceSettings'
import { api } from './api'
import {
  filterSkills, groupSkills, inventoryNote, skillLabel, skillTitle,
  type AgentSkill, type SkillInventory,
} from './skills'
import type { Session } from './types'

// The command rail's long tail, as a grid, plus the agent's own skills.
//
// The strip under a terminal holds what you hammer (Esc, Enter, arrows, ^C); it is
// horizontally scarce, which is why several built-ins used to ship switched off.
// Everything else — extra keys, skills, slash commands, literal text snippets —
// lives here, where room is not the constraint and items can carry full labels.
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

type Props = {
  session: Session | null
  onDone: () => void
  onOpenSettings: () => void
}

function dispatch(sessionId: string, action: string, detail: Record<string, unknown> = {}) {
  window.dispatchEvent(new CustomEvent('mux:terminal-action', { detail: { sessionId, action, ...detail } }))
}

/** Built-in action items the drawer can run; the rest are injection types. */
const ACTION_LABELS: Record<string, string> = {
  paste: 'Paste',
  copyReply: 'Copy reply',
  copyResume: 'Copy resume',
  branch: 'Branch',
  relaunch: 'Relaunch',
  toggleKeyboard: 'Keyboard',
  clipboardHistory: 'Clipboard',
  endSession: 'End session',
}

export function CommandsTab({ session, onDone, onOpenSettings }: Props) {
  const [note, setNote] = useState('')
  // End session is the one item here that arms a confirm rather than acting, so this
  // tab mirrors the workspace's armed id (broadcast by App) to label the second click.
  const [killArmed, setKillArmed] = useState(false)
  const [inventory, setInventory] = useState<SkillInventory | null>(null)
  const [skillsError, setSkillsError] = useState('')
  const [query, setQuery] = useState('')
  const backend = (session?.backend || 'shell') as RailBackend
  const isAgent = backend === 'claude' || backend === 'codex'
  const items = useMemo(
    () => resolveRail(loadRailItems(session?.project_id), { platform: currentProfile(), backend }, 'drawer'),
    [session?.project_id, backend],
  )
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

  if (!session) {
    return <>
      <p class="drawer-status">no terminal focused</p>
      <p class="drawer-empty">Session commands act on a terminal. Focus one and reopen this tab.</p>
    </>
  }

  const run = (item: RailItem) => {
    // Prompt templates are fetched from the library at click time, so this one path
    // is asynchronous: it closes the drawer only once the insert (or the hand-off to
    // the Prompts tab for variable filling) has actually happened.
    if (item.type === 'prompt') {
      void activatePromptRailItem(item, { sessionId: session.id, projectId: session.project_id })
        .then(problem => { if (problem) setNote(problem); else onDone() })
      return
    }
    if (item.type === 'key') dispatch(session.id, 'sendKey', { text: item.bytes || '' })
    else if (item.type === 'action') {
      // `clipboardHistory` is the drawer itself; running it from inside the drawer
      // would be a no-op, so it is filtered out of this grid entirely.
      dispatch(session.id, item.action === 'toggleKeyboard' ? 'toggleKeyboard' : item.action || '', {})
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
    // Inserted, never submitted: a skill invoked bare runs with no context, and the
    // point of typing it into a live composer is to say what it should act on.
    dispatch(session.id, 'insertText', { text: skill.invocation, submit: false })
    onDone()
  }

  const visible = items.filter(item => item.id !== 'clipboardHistory')
  const keys = visible.filter(item => item.type === 'key')
  const rest = visible.filter(item => item.type !== 'key')
  const label = (item: RailItem) =>
    item.action === 'endSession' && killArmed ? 'Confirm ✓'
      : item.type === 'action' ? ACTION_LABELS[item.action || ''] || item.label : item.label
  const matched = inventory ? filterSkills(inventory.skills, query) : []
  const groups = groupSkills(matched)
  const disclosure = inventoryNote(inventory)

  return <>
    <p class="drawer-status">{session.name || session.id} · {backend}</p>
    {rest.length > 0 && <div class="drawer-grid" role="group" aria-label="Session commands">
      {rest.map(item => <button key={item.id} class={item.action === 'endSession' && killArmed ? 'confirming' : undefined} title={item.title || (item.type === 'prompt' ? 'Insert this prompt template into the composer' : railPayload(item, backend)) || item.label} onClick={() => run(item)}>
        <span>{label(item)}</span>
        {item.type !== 'action' && <small>{item.type === 'skill' ? 'skill' : item.type === 'slash' ? 'command' : item.type === 'prompt' ? 'prompt' : 'text'}{item.type !== 'prompt' && item.submit ? ' · sends' : ''}</small>}
      </button>)}
    </div>}
    {keys.length > 0 && <div class="drawer-grid keys" role="group" aria-label="Terminal keys">
      {keys.map(item => <button key={item.id} title={item.title || item.label} onClick={() => run(item)}><span>{item.label}</span></button>)}
    </div>}
    {!visible.length && <p class="drawer-empty">Nothing is assigned to the drawer for this session. Move rail items here from Settings → Command rail.</p>}
    {isAgent && <section class="drawer-skills">
      <header>
        <h4>{backend === 'codex' ? 'Codex skills' : 'Claude skills'}</h4>
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
          {group.skills.map(skill => <button
            key={skill.path}
            class={skill.shadowed_by ? 'shadowed' : undefined}
            title={skillTitle(skill)}
            onClick={() => insertSkill(skill)}
          >
            <span>
              <code>{skill.invocation}</code>
              {skillLabel(skill) !== skill.name && <em>{skillLabel(skill)}</em>}
              {skill.added_after_start && <b class="skill-flag warn" title="Added after this session started">new</b>}
              {!skill.implicit && <b class="skill-flag" title="Explicit-only: the agent never invokes this on its own">explicit</b>}
            </span>
            <small>{skill.short_description || skill.description || skill.origin}</small>
          </button>)}
        </div>)}
        {disclosure && <p class="drawer-skill-note">{disclosure}</p>}
      </div>
    </section>}
    {note && <p class="clipboard-note" aria-live="polite">{note}</p>}
    <footer class="drawer-actions"><button onClick={onOpenSettings}>Edit command rail</button></footer>
  </>
}
