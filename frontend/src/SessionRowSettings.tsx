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

import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { SessionRowBody } from './SessionRowBody'
import { StateIndicator } from './StateIndicator'
import { currentProfile, type SettingsProfile } from './deviceSettings'
import {
  DOT_SIZE_MAX, DOT_SIZE_MIN, ROW_FIELDS, ROW_FIELD_BY_ID, ROW_PRESETS, SEPARATORS, SEPARATOR_IDS,
  defaultSessionRowConfig, lineConfig, normalizeDotSize, placeField, presetConfig, removeField,
  setFieldMode, unplacedFields,
  type ContextRender, type CountStyle, type DiffStyle, type DotShape,
  type RowAlign, type RowFieldId, type RowLine, type RowPresetId, type SeparatorId,
  type SessionRowConfig, type StandingRender,
} from './sessionRowConfig'
import {
  buildSessionRowTokens, deriveRowContext, sessionContextArc, sessionStandingMark,
} from './sessionRowFields'
import { loadSessionRowConfig, saveSessionRowConfig, useRowBudget } from './sessionRowPrefs'
import {
  SIDEBAR_DEFAULT_WIDTH, SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH, clampSidebarWidth,
} from './sidebarResize'
import type { Session } from './types'

const NOW = 1_770_000_000

const sample = (overrides: Partial<Session>): Session => ({
  id: 'preview', name: 'preview', project_id: 'p1', backend: 'claude',
  state: 'idle', state_since: NOW - 30, created_at: NOW - 7200,
  context_pct: 0, context_peak_pct: 0, compaction_count: 0, cost_usd: 0,
  git: { branch: 'master', dirty: 0, ahead: 0, behind: 0 },
  ...overrides,
} as unknown as Session)

// The first three share one checkout, which is not incidental: it is the state
// the shared-checkout mark exists to report, and a preview of four sessions in
// four repositories would never draw it.
const PRIMARY_ROOT = 'D:/PROJECTS/example'

const PREVIEW_SESSIONS: Session[] = [
  sample({
    id: 'preview-working', name: 'refactor tokenizer', backend: 'codex', model: 'gpt-5-codex',
    state: 'working', state_detail: 'apply_patch', state_since: NOW - 1320,
    context_pct: 0.74, context_peak_pct: 0.74,
    git: {
      branch: 'feat-tokenizer', dirty: 7, ahead: 2, behind: 0, added: 312, removed: 48,
      root: PRIMARY_ROOT, compare_ref: 'origin/main',
      compare_added: 486, compare_removed: 91, compare_files: 12,
    },
    provider_account_hashes: { openai: 'a1b2c3d4e5f6' },
  }),
  sample({
    id: 'preview-ready', name: 'status detection v2', model: 'opus',
    state: 'idle', last_turn_ms: 72_000, context_pct: 0.41, context_peak_pct: 0.55,
    // A finished turn with a half-written reply still in the composer: the case
    // the unsent-input mark exists for, and the one you cannot see any other way
    // without opening the pane.
    unsent_input: { since: NOW - 240 },
    git: {
      branch: 'feat-tokenizer', dirty: 7, ahead: 2, behind: 0, added: 312, removed: 48,
      root: PRIMARY_ROOT, compare_ref: 'origin/main',
      compare_added: 486, compare_removed: 91, compare_files: 12,
    },
  }),
  sample({
    id: 'preview-awaiting', name: 'push notifications', model: 'opus',
    state: 'awaiting', awaiting_reason: 'approval', state_detail: 'Bash(rm -rf)',
    state_since: NOW - 300, context_pct: 0.63, context_peak_pct: 0.63,
    git: {
      branch: 'feat-tokenizer', dirty: 7, ahead: 2, behind: 0, added: 312, removed: 48,
      root: PRIMARY_ROOT, compare_ref: 'origin/main',
      compare_added: 486, compare_removed: 91, compare_files: 12,
    },
  }),
  sample({
    id: 'preview-worktree', name: 'audit sweep', backend: 'codex', model: 'gpt-5-codex',
    state: 'working', state_since: NOW - 10_800, context_pct: 0.96, context_peak_pct: 0.97,
    compaction_count: 3, cost_usd: 4.2,
    standing_activity: [{
      kind: 'subagents', source: 'hook', evidence: 'hook:SubagentStart',
      since: NOW - 600, expires_at: null, count: 3, detail: null,
    }],
    git: {
      branch: 'wt-audit', worktree: 'wt-audit', dirty: 3, ahead: 0, behind: 1,
      added: 18, removed: 4, root: `${PRIMARY_ROOT}-wt/wt-audit`, compare_ref: 'origin/main',
      compare_added: 204, compare_removed: 37, compare_files: 6,
    },
    provider_account_hashes: { openai: '99aa88bb77cc' },
  }),
]

/**
 * Everything about the preview except how much room it has.
 *
 * The budget is measured off the preview itself, so the panel demonstrates the
 * width ladder instead of hiding it: this preview used to render at a fixed 420px
 * — wider than the sidebar can be dragged — so the one behaviour a reader cannot
 * predict from the field list was the one behaviour it never showed.
 */
const PREVIEW_FLEET = deriveRowContext(PREVIEW_SESSIONS, { 'preview-worktree': 2 }, NOW)

const SHAPES: Array<{ id: DotShape; label: string }> = [
  { id: 'hexagon', label: 'Hexagon' }, { id: 'circle', label: 'Circle' }, { id: 'square', label: 'Square' },
]
const SIZE_PROFILES: Array<{ id: SettingsProfile; label: string }> = [
  { id: 'desktop', label: 'Size on desktop' }, { id: 'mobile', label: 'Size on mobile' },
]
const sizeKey = (profile: SettingsProfile): 'dotSizeDesktop' | 'dotSizeMobile' =>
  profile === 'mobile' ? 'dotSizeMobile' : 'dotSizeDesktop'
/** How long a continuous control must rest before its value is persisted. */
const SETTLE_MS = 250

const CONTEXT_MODES: Array<{ id: ContextRender; label: string; hint: string }> = [
  { id: 'arc', label: 'Around the indicator', hint: 'Costs no row width; peak marked on the outline.' },
  { id: 'gauge', label: 'Gauge', hint: 'Four cells in the row, comparable down the list.' },
  { id: 'percent', label: 'Percentage', hint: 'Exact number in the row.' },
  { id: 'off', label: 'Off', hint: 'Context pressure is not shown.' },
]

const STANDING_MODES: Array<{ id: StandingRender; label: string; hint: string }> = [
  { id: 'row', label: 'Glyphs in the flag strip', hint: 'Says which kind and how many (⟳ loop or cron, ≡ background tasks, ⑂ subagents).' },
  { id: 'indicator', label: 'Pip on the indicator', hint: 'Costs no row width at all, and says only that something is standing — the kinds and counts move to the tooltip.' },
  { id: 'off', label: 'Off', hint: 'Standing activity is not marked on the row.' },
]

export function SessionRowSettings() {
  const [config, setConfig] = useState(loadSessionRowConfig)
  const [error, setError] = useState('')
  const thisDevice = currentProfile()
  // Which device class the preview is drawn at. Starts on this device and
  // follows whichever slider was last touched, so dragging the mobile size from
  // a desktop browser shows the mobile result instead of leaving the preview
  // inert and the change unverifiable until you pick up the phone.
  const [sizeProfile, setSizeProfile] = useState<SettingsProfile>(thisDevice)
  // The width the preview is drawn at, and therefore the width the ladder is
  // demonstrated at. Device-local and unpersisted on purpose: it is an inspection
  // control for this visit to the panel, not a property of the layout.
  const [previewWidth, setPreviewWidth] = useState(SIDEBAR_DEFAULT_WIDTH)
  const previewMetricRef = useRef<HTMLDivElement>(null)
  const previewBudget = useRowBudget(previewMetricRef)
  const previewContext = useMemo(
    () => ({ ...PREVIEW_FLEET, budget: previewBudget }),
    [previewBudget],
  )

  useEffect(() => {
    const sync = () => setConfig(loadSessionRowConfig())
    window.addEventListener('mux:settings-changed', sync)
    return () => window.removeEventListener('mux:settings-changed', sync)
  }, [])

  const save = (next: SessionRowConfig) => {
    saveSessionRowConfig(next)
      .then(() => setError(''))
      .catch(() => setError('Could not save the row layout. The daemon may be restarting; try again.'))
  }

  const change = (next: SessionRowConfig) => {
    setConfig(next)
    save(next)
  }

  // A slider emits an event per pixel of travel, and every placement edit here
  // is one `PUT /api/settings`. Continuous controls therefore apply locally at
  // once — the preview has to track the drag — and persist once the drag settles.
  const settleTimer = useRef<number>()
  const changeContinuous = (next: SessionRowConfig) => {
    setConfig(next)
    if (settleTimer.current) window.clearTimeout(settleTimer.current)
    settleTimer.current = window.setTimeout(() => save(next), SETTLE_MS)
  }
  useEffect(() => () => { if (settleTimer.current) window.clearTimeout(settleTimer.current) }, [])

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
    {/* The preview carries the size of whichever device class is being edited,
        rather than the root variable this device is using, so adjusting the
        mobile size from a desktop browser still shows what it did. */}
    <label class="row-size-control">
      <span>Sidebar width</span>
      <input
        type="range"
        min={SIDEBAR_MIN_WIDTH}
        max={SIDEBAR_MAX_WIDTH}
        step={1}
        value={previewWidth}
        aria-label="Preview sidebar width in pixels"
        onInput={event => setPreviewWidth(clampSidebarWidth(event.currentTarget.valueAsNumber))}
      />
      <output>{previewWidth}px</output>
    </label>
    <div
      class="session-row-preview sidebar"
      style={{ '--session-dot': `${config[sizeKey(sizeProfile)]}px`, width: `${previewWidth}px` }}
    >
      <div class="session-list">
        {/* Same probe the sidebar renders, so the preview's budget is measured
            the same way rather than computed from the slider's number: the row's
            text column is the slider minus a gutter the indicator size sets. */}
        <div ref={previewMetricRef} class="row-metric-probe" aria-hidden="true">
          <div class="row-metric">
            <span />
            <span class="session-copy" data-metric="copy">
              <span class="row-line top"><span data-metric="top">0000000000</span></span>
              <span class="row-line bottom"><span data-metric="bottom">0000000000</span></span>
            </span>
          </div>
        </div>
        {PREVIEW_SESSIONS.map(session => <div key={session.id} class={`session-row agent ${session.state}`}>
          <StateIndicator
            session={session}
            shape={config.dotShape}
            gauge={sessionContextArc(session, config)}
            standing={sessionStandingMark(session, config)}
          />
          <SessionRowBody session={session} tokens={buildSessionRowTokens(session, config, previewContext)} config={config} />
        </div>)}
      </div>
    </div>

    <h4>State indicator</h4>
    <label>Shape
      <select value={config.dotShape} onChange={event => change({ ...config, dotShape: event.currentTarget.value as DotShape })}>
        {SHAPES.map(shape => <option key={shape.id} value={shape.id}>{shape.label}</option>)}
      </select>
    </label>
    {SIZE_PROFILES.map(profile => <label key={profile.id} class="row-size-control">
      <span>
        {profile.label}
        {profile.id === thisDevice ? ' (this device)' : ''}
      </span>
      <input
        type="range"
        min={DOT_SIZE_MIN}
        max={DOT_SIZE_MAX}
        step={1}
        value={config[sizeKey(profile.id)]}
        aria-label={`${profile.label} indicator size in pixels`}
        onInput={event => {
          setSizeProfile(profile.id)
          changeContinuous({
            ...config,
            [sizeKey(profile.id)]:
              normalizeDotSize(event.currentTarget.valueAsNumber, config[sizeKey(profile.id)]),
          })
        }}
      />
      <output>{config[sizeKey(profile.id)]}px</output>
    </label>)}
    <p>The indicator sizes the sidebar row around it: the gutter column, the context ring, the stack thread, and the row's own height are all derived from this number, so a larger indicator gives a taller row rather than a clipped one. Desktop and mobile are separate because the same size is not read the same way at desk distance and at arm's length.</p>
    <label>Context pressure
      <select value={config.context} onChange={event => change({ ...config, context: event.currentTarget.value as ContextRender })}>
        {CONTEXT_MODES.map(mode => <option key={mode.id} value={mode.id}>{mode.label}</option>)}
      </select>
    </label>
    <p>{CONTEXT_MODES.find(mode => mode.id === config.context)?.hint} The indicator's colour, pulse, and hollow "engaged" variant are not configurable: they are the one thing every surface reads the same way.</p>
    <label>Standing activity
      <select value={config.standing} onChange={event => change({ ...config, standing: event.currentTarget.value as StandingRender })}>
        {STANDING_MODES.map(mode => <option key={mode.id} value={mode.id}>{mode.label}</option>)}
      </select>
    </label>
    <p>{STANDING_MODES.find(mode => mode.id === config.standing)?.hint} Whichever you pick applies to tab strips and menus too, so the same session never reports it twice on one screen.</p>

    {lineEditor('top', 'Top line', 'Identity, plus the flag strip on the right: presence-only marks are pinned to the row’s edge so a long title ellipsizes instead of hiding them.')}
    {lineEditor('bottom', 'Bottom line', 'Everything else. The state word is off by default because the indicator already carries it.')}

    <h4>Git fields</h4>
    <p>Every Git number describes the <em>checkout</em> a session is working in, never the session: <code>git status</code> answers for the whole repository however it is invoked, so sessions sharing a working tree necessarily report the same figures. A value shared by more than one live session is underlined, and its tooltip says how many.</p>
    <p><strong>Uncommitted</strong> is measured against HEAD, so it drops to zero as soon as a session commits. <strong>Branch</strong> (marked ⎇) is measured from the merge base with the project's comparison ref, so it keeps counting committed work — usually the number you want for a worktree-per-branch fleet. Both stay blank rather than showing zero when they could not be measured.</p>

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
