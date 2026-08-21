import { useEffect, useState } from 'preact/hooks'

import { api } from './api'
import { RailDropup } from './RailDropup'
import {
  groupSkills, inventoryNote, skillLabel, skillTitle,
  type AgentSkill, type SkillInventory,
} from './skills'

// The focused session's own skills, one tap from its composer.
//
// Everything about *what* a skill is stays in `skills.ts`, which the Actions
// drawer's Skills section already renders from. This is a second view of the same
// inventory, not a second inventory: same endpoint, same grouping order, same
// caveats. The endpoint caches for ten seconds, so opening the drop-up repeatedly
// is a cache read rather than a filesystem walk.
//
// Flat rather than grouped, and that is the one deliberate difference from the
// drawer section. A group heading costs a row, and with five rows on screen two
// headings would leave three skills visible. So the scope rides each row as a
// tag, in the same precedence order the headings would have imposed.
//
// Three caveats have to survive to the button or the list misleads:
//   * `added_after_start` — the file is newer than the running CLI, so typing the
//     invocation will not work until the agent is restarted.
//   * `implicit: false` — invocable by name, but the model never reaches for it.
//   * `builtin_skills_hidden` — Claude's built-ins are compiled into the CLI and
//     cannot be enumerated, so a bare list quietly implies they do not exist.
// `skillTitle` and `inventoryNote` compose all of them already.

type Props = {
  sessionId: string
  /** Harness display name, for the empty and error copy. */
  harness: string
  anchor: HTMLElement | null
  onClose: () => void
  /** Insert the invocation into the pane's composer. Never submits. */
  onInsert: (text: string) => Promise<void>
  /** Open the Skills section of the Actions drawer. */
  onOpenSection: () => void
}

/** Scope order and labels come from `groupSkills`; the rows are then flattened. */
function flatten(skills: AgentSkill[]): Array<{ skill: AgentSkill; scope: string }> {
  return groupSkills(skills).flatMap(group => group.skills.map(skill => ({ skill, scope: group.label })))
}

export function SkillsDropup({ sessionId, harness, anchor, onClose, onInsert, onOpenSection }: Props) {
  const [inventory, setInventory] = useState<SkillInventory | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')

  useEffect(() => {
    let live = true
    setInventory(null)
    setError('')
    api<SkillInventory>('GET', `/api/sessions/${encodeURIComponent(sessionId)}/skills`, undefined, { timeoutMs: 20_000 })
      .then(result => { if (live) setInventory(result) })
      .catch(cause => { if (live) setError(cause instanceof Error ? cause.message : String(cause)) })
    return () => { live = false }
  }, [sessionId])

  const insert = async (skill: AgentSkill) => {
    setBusy(skill.path)
    setError('')
    try {
      // Inserted, never submitted — a skill invoked bare runs with no context, and
      // the point of typing it into a live composer is to say what it acts on.
      await onInsert(skill.invocation)
      onClose()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy('')
    }
  }

  const rows = inventory ? flatten(inventory.skills) : []
  const disclosure = inventoryNote(inventory)
  return <RailDropup
    label={`${harness} skills`}
    anchor={anchor}
    onClose={onClose}
    sticky={{
      label: 'All skills…',
      title: 'Open the Skills section of the Actions drawer, where skills can be filtered, read and pinned',
      run: onOpenSection,
    }}
  >
    {!inventory && !error && <p class="rail-dropup-empty">Reading skill directories…</p>}
    {error && <p class="rail-dropup-empty">Skills could not be read: {error}</p>}
    {inventory && !rows.length && <p class="rail-dropup-empty">No skills on disk for this session.</p>}
    {rows.map(({ skill, scope }) => <button
      key={skill.path}
      type="button"
      class={`rail-dropup-row${skill.shadowed_by ? ' shadowed' : ''}`}
      disabled={!!busy}
      title={skillTitle(skill)}
      onClick={() => void insert(skill)}
    >
      <span>
        <code>{skill.invocation}</code>
        {skillLabel(skill) !== skill.name && <em>{skillLabel(skill)}</em>}
        {skill.added_after_start && <b class="skill-flag warn">new</b>}
        {!skill.implicit && <b class="skill-flag">explicit</b>}
      </span>
      <small>{scope} · {skill.short_description || skill.description || skill.origin}</small>
    </button>)}
    {disclosure && <p class="rail-dropup-note">{disclosure}</p>}
  </RailDropup>
}
