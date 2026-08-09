// Settings → Appearance → Session rows.
//
// The row is configured by placing fields, not by ticking boxes: where a field
// sits and whether it is on are one decision, so the panel cannot produce the
// state "enabled but nowhere". Placement uses explicit move controls rather than
// drag-and-drop — the same edit has to work from a phone and from a keyboard,
// and a two-axis drop target satisfies neither.
//
// The live preview is not decoration. Most fields are configured as `notable`,
// so a static mock-up of one healthy session would show almost nothing and tell
// the user nothing about what they just changed; the four sample sessions here
// are chosen so each mode has something to demonstrate.

import { useEffect, useState } from 'preact/hooks'
import { SessionRowBody } from './SessionRowBody'
import { StateIndicator } from './StateIndicator'
import {
  ROW_FIELDS, ROW_FIELD_BY_ID, ROW_PRESETS, SEPARATORS, SEPARATOR_IDS,
  defaultSessionRowConfig, lineConfig, placeField, presetConfig, removeField, setFieldMode,
  unplacedFields,
  type ContextRender, type CountStyle, type DiffStyle, type DotShape,
  type RowAlign, type RowFieldId, type RowLine, type RowPresetId, type SeparatorId,
  type SessionRowConfig,
} from './sessionRowConfig'
import { buildSessionRowTokens, deriveRowContext, sessionContextArc } from './sessionRowFields'
import { loadSessionRowConfig, saveSessionRowConfig } from './sessionRowPrefs'
import type { Session } from './types'

const NOW = 1_770_000_000

const sample = (overrides: Partial<Session>): Session => ({
  id: 'preview', name: 'preview', project_id: 'p1', backend: 'claude',
  state: 'idle', state_since: NOW - 30, created_at: NOW - 7200,
  context_pct: 0, context_peak_pct: 0, compaction_count: 0, cost_usd: 0,
  git: { branch: 'master', dirty: 0, ahead: 0, behind: 0 },
  ...overrides,
} as unknown as Session)

const PREVIEW_SESSIONS: Session[] = [
  sample({
    id: 'preview-working', name: 'refactor tokenizer', backend: 'codex', model: 'gpt-5-codex',
    state: 'working', state_detail: 'apply_patch', state_since: NOW - 1320,
    context_pct: 0.74, context_peak_pct: 0.74,
    git: { branch: 'feat-tokenizer', dirty: 7, ahead: 2, behind: 0, added: 312, removed: 48 },
    provider_account_hashes: { openai: 'a1b2c3d4e5f6' },
  }),
  sample({
    id: 'preview-ready', name: 'status detection v2', model: 'opus',
    state: 'idle', last_turn_ms: 72_000, context_pct: 0.41, context_peak_pct: 0.55,
  }),
  sample({
    id: 'preview-awaiting', name: 'push notifications', model: 'opus',
    state: 'awaiting', awaiting_reason: 'approval', state_detail: 'Bash(rm -rf)',
    state_since: NOW - 300, context_pct: 0.63, context_peak_pct: 0.63,
  }),
  sample({
    id: 'preview-worktree', name: 'audit sweep', backend: 'codex', model: 'gpt-5-codex',
    state: 'working', state_since: NOW - 10_800, context_pct: 0.96, context_peak_pct: 0.97,
    compaction_count: 3, cost_usd: 4.2,
    git: { branch: 'wt-audit', worktree: 'wt-audit', dirty: 3, ahead: 0, behind: 1, added: 18, removed: 4 },
    provider_account_hashes: { openai: '99aa88bb77cc' },
  }),
]

const PREVIEW_CONTEXT = deriveRowContext(PREVIEW_SESSIONS, { 'preview-worktree': 2 }, NOW)

const SHAPES: Array<{ id: DotShape; label: string }> = [
  { id: 'hexagon', label: 'Hexagon' }, { id: 'circle', label: 'Circle' }, { id: 'square', label: 'Square' },
]
const CONTEXT_MODES: Array<{ id: ContextRender; label: string; hint: string }> = [
  { id: 'arc', label: 'Around the indicator', hint: 'Costs no row width; peak marked on the outline.' },
  { id: 'gauge', label: 'Gauge', hint: 'Four cells in the row, comparable down the list.' },
  { id: 'percent', label: 'Percentage', hint: 'Exact number in the row.' },
  { id: 'off', label: 'Off', hint: 'Context pressure is not shown.' },
]

export function SessionRowSettings() {
  const [config, setConfig] = useState(loadSessionRowConfig)
  const [error, setError] = useState('')

  useEffect(() => {
    const sync = () => setConfig(loadSessionRowConfig())
    window.addEventListener('mux:settings-changed', sync)
    return () => window.removeEventListener('mux:settings-changed', sync)
  }, [])

  const change = (next: SessionRowConfig) => {
    setConfig(next)
    saveSessionRowConfig(next)
      .then(() => setError(''))
      .catch(() => setError('Could not save the row layout. The daemon may be restarting; try again.'))
  }

  const move = (line: RowLine, align: RowAlign, id: RowFieldId, offset: number) => {
    const slots = lineConfig(config, line)[align]
    const index = slots.findIndex(slot => slot.id === id)
    if (index < 0) return
    change(placeField(config, id, line, align, index + offset))
  }

  const slotRow = (line: RowLine, align: RowAlign, id: RowFieldId, index: number, total: number) => {
    const descriptor = ROW_FIELD_BY_ID[id]
    const slots = lineConfig(config, line)[align]
    const mode = slots[index].mode
    const other: RowAlign = align === 'left' ? 'right' : 'left'
    return <li key={id} class="row-slot">
      <span class="row-slot-name">{descriptor.label}</span>
      <select
        aria-label={`${descriptor.label} visibility`}
        title={`Notable: ${descriptor.notable}`}
        value={mode}
        onChange={event => change(setFieldMode(config, id, event.currentTarget.value === 'always' ? 'always' : 'notable'))}
      >
        <option value="notable">when notable</option>
        <option value="always">always</option>
      </select>
      <span class="row-slot-actions">
        <button type="button" title="Move earlier" disabled={index === 0} onClick={() => move(line, align, id, -1)}>↑</button>
        <button type="button" title="Move later" disabled={index === total - 1} onClick={() => move(line, align, id, 1)}>↓</button>
        <button type="button" title={`Move to the ${other} side`} onClick={() => change(placeField(config, id, line, other))}>{align === 'left' ? '→' : '←'}</button>
        <button type="button" class="danger" title="Remove from the row" disabled={id === 'title'} onClick={() => change(removeField(config, id))}>×</button>
      </span>
    </li>
  }

  const section = (line: RowLine, align: RowAlign) => {
    const slots = lineConfig(config, line)[align]
    return <div class="row-section-editor">
      <h5>{align === 'left' ? 'Left' : 'Right'}</h5>
      {slots.length
        ? <ul>{slots.map((slot, index) => slotRow(line, align, slot.id, index, slots.length))}</ul>
        : <p class="row-section-empty">Nothing placed here.</p>}
    </div>
  }

  const lineEditor = (line: RowLine, heading: string, note: string) => {
    const source = lineConfig(config, line)
    const available = unplacedFields(config).filter(field => Boolean(field.identity) === (line === 'top'))
    return <div class="row-line-editor">
      <h4>{heading}</h4>
      <p>{note}</p>
      <label>Separator
        <select value={source.separator} onChange={event => change({
          ...config,
          [line]: { ...source, separator: event.currentTarget.value as SeparatorId },
        })}>
          {SEPARATOR_IDS.map(id => <option key={id} value={id}>{SEPARATORS[id].label}</option>)}
        </select>
      </label>
      <div class="row-section-grid">{section(line, 'left')}{section(line, 'right')}</div>
      {available.length > 0 && <div class="row-field-pool">
        <span>Add:</span>
        {available.map(field => <button
          key={field.id}
          type="button"
          title={`Notable: ${field.notable}`}
          onClick={() => change(placeField(config, field.id, line, 'left'))}
        >{field.label}</button>)}
      </div>}
    </div>
  }

  return <div class="session-row-settings">
    <h3>Session rows</h3>
    <p>The sidebar row is two lines, each with a left and a right section. A field placed nowhere is off. <strong>When notable</strong> shows a field only when its value is worth reading, so a quiet fleet stays quiet and anything visible earned its place.</p>
    {error && <p class="settings-inline-error" aria-live="polite">{error}</p>}

    <div class="theme-actions">
      {ROW_PRESETS.map(preset => <button
        key={preset.id}
        type="button"
        title={preset.description}
        onClick={() => change(presetConfig(preset.id as RowPresetId))}
      >{preset.label}</button>)}
      <button type="button" onClick={() => change(defaultSessionRowConfig())}>Reset to default</button>
    </div>

    <h4>Preview</h4>
    <div class="session-row-preview sidebar">
      <div class="session-list">
        {PREVIEW_SESSIONS.map(session => <div key={session.id} class={`session-row agent ${session.state}`}>
          <StateIndicator session={session} shape={config.dotShape} gauge={sessionContextArc(session, config)} />
          <SessionRowBody session={session} tokens={buildSessionRowTokens(session, config, PREVIEW_CONTEXT)} config={config} />
        </div>)}
      </div>
    </div>

    <h4>State indicator</h4>
    <label>Shape
      <select value={config.dotShape} onChange={event => change({ ...config, dotShape: event.currentTarget.value as DotShape })}>
        {SHAPES.map(shape => <option key={shape.id} value={shape.id}>{shape.label}</option>)}
      </select>
    </label>
    <label>Context pressure
      <select value={config.context} onChange={event => change({ ...config, context: event.currentTarget.value as ContextRender })}>
        {CONTEXT_MODES.map(mode => <option key={mode.id} value={mode.id}>{mode.label}</option>)}
      </select>
    </label>
    <p>{CONTEXT_MODES.find(mode => mode.id === config.context)?.hint} The indicator's colour, pulse, and hollow "engaged" variant are not configurable: they are the one thing every surface reads the same way.</p>

    {lineEditor('top', 'Top line', 'Identity: the indicator sits outside these sections and is always drawn.')}
    {lineEditor('bottom', 'Bottom line', 'Everything else. The state word is off by default because the indicator already carries it.')}

    <h4>Token style</h4>
    <label>Lines changed
      <select value={config.diffStyle} onChange={event => change({ ...config, diffStyle: event.currentTarget.value as DiffStyle })}>
        <option value="numbers">Numbers (+312 -48)</option>
        <option value="bar">Split bar</option>
      </select>
    </label>
    <label>Small counts
      <select value={config.countStyle} onChange={event => change({ ...config, countStyle: event.currentTarget.value as CountStyle })}>
        <option value="numbers">Numbers</option>
        <option value="pips">Pips up to four, then numbers</option>
      </select>
    </label>
    <label class="check"><span>Prefix git tokens with a glyph (⎇ branch, ⌂ worktree)</span>
      <input type="checkbox" checked={config.gitGlyphs} onChange={event => change({ ...config, gitGlyphs: event.currentTarget.checked })} />
    </label>

    <h4>Mobile</h4>
    <label class="check"><span>Show the same fields on mobile</span>
      <input type="checkbox" checked={config.mobileFields} onChange={event => change({ ...config, mobileFields: event.currentTarget.checked })} />
    </label>
    <p>Off, the phone renders identity only — indicator, provider mark, title — which is what it has always shown. On, mobile rows carry the configured fields, shedding the lowest-priority ones as the width runs out. The layout itself is shared: both screens want the same information in the same order, and only how much of it fits differs.</p>

    <p>Fields the row can draw: {ROW_FIELDS.map(field => field.label).join(', ')}.</p>
  </div>
}
