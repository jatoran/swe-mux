import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
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
import { Dropdown } from './Dropdown'
import { SessionRowBody } from './SessionRowBody'
import { StateIndicator } from './StateIndicator'
import { currentProfile, type SettingsProfile } from './deviceSettings'
import {
  DOT_SIZE_MAX, DOT_SIZE_MIN, ROW_FIELDS, ROW_FIELD_BY_ID, ROW_PRESETS, SEPARATORS, SEPARATOR_IDS,
  defaultSessionRowConfig, lineConfig, normalizeDotSize, normalizeSessionRowConfig, placeField,
  presetConfig, removeField, setFieldMode, unplacedFields,
  type ContextRender, type CountStyle, type CwdStyle, type DiffStyle, type DotShape,
  type RowAlign, type RowFieldId, type RowLine, type RowPresetId, type SeparatorId,
  type SessionRowConfig, type StandingRender,
} from './sessionRowConfig'
import {
  buildSessionRowTokens, deriveRowContext, sessionContextArc, sessionStandingMark,
} from './sessionRowFields'
import { loadSessionRowConfig, saveSessionRowConfig, useRowBudget } from './sessionRowPrefs'
import {
  SIDEBAR_WIDTH_KEY, storedSidebarWidth,
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

// One session per context band, in ramp order, so the preview demonstrates the
// whole scale while the thresholds beside it are being dragged. Four sessions
// and four bands is a coincidence worth spending: a preview that showed only the
// upper half made the first threshold the one control you could not see working.
const PREVIEW_CONTEXT = { calm: 0.22, warn: 0.48, high: 0.68, crit: 0.94 }

const PREVIEW_SESSIONS: Session[] = [
  sample({
    id: 'preview-working', get name() { return t("SessionRowSettings.6d23437fd8") }, backend: 'codex', model: 'gpt-5-codex',
    state: 'working', state_detail: 'apply_patch', state_since: NOW - 1320,
    // The one session in the preview that speaks, so the read-aloud mark has something to
    // draw. `auto` rather than `on_demand`: the accent belongs to the mode that makes a
    // sound without being asked, and a preview showing only the muted rendering would
    // teach the wrong thing about what the mark means.
    voice_mode: 'auto',
    context_pct: PREVIEW_CONTEXT.warn, context_peak_pct: PREVIEW_CONTEXT.warn,
    // Mid-turn, so the total is the completed sum plus the turn running now and
    // the preview shows the field's live rendering rather than a frozen one.
    worked_ms: 41 * 60_000, turn_started_at: NOW - 1320,
    git: {
      branch: 'feat-tokenizer', dirty: 7, ahead: 2, behind: 0, added: 312, removed: 48,
      root: PRIMARY_ROOT, compare_ref: 'origin/main',
      compare_added: 486, compare_removed: 91, compare_files: 12,
    },
    provider_account_hashes: { openai: 'a1b2c3d4e5f6' },
  }),
  sample({
    id: 'preview-ready', get name() { return t("SessionRowSettings.b33cd8e682") }, model: 'opus',
    state: 'idle', last_turn_ms: 72_000,
    context_pct: PREVIEW_CONTEXT.calm, context_peak_pct: 0.55,
    worked_ms: 9 * 60_000,
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
    id: 'preview-awaiting', get name() { return t("SessionRowSettings.29e77a40bc") }, model: 'opus',
    state: 'awaiting', awaiting_reason: 'approval', state_detail: 'Bash(rm -rf)',
    state_since: NOW - 300,
    context_pct: PREVIEW_CONTEXT.high, context_peak_pct: PREVIEW_CONTEXT.high,
    worked_ms: 26 * 60_000, turn_started_at: NOW - 300,
    git: {
      branch: 'feat-tokenizer', dirty: 7, ahead: 2, behind: 0, added: 312, removed: 48,
      root: PRIMARY_ROOT, compare_ref: 'origin/main',
      compare_added: 486, compare_removed: 91, compare_files: 12,
    },
  }),
  sample({
    id: 'preview-worktree', get name() { return t("SessionRowSettings.4e585d2766") }, backend: 'codex', model: 'gpt-5-codex',
    state: 'working', state_since: NOW - 10_800,
    context_pct: PREVIEW_CONTEXT.crit, context_peak_pct: 0.97,
    worked_ms: 3 * 3600_000, turn_started_at: NOW - 10_800,
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
 * width ladder at the current device's real navigation width. This keeps the
 * result tied to the layout the operator is actually using.
 */
const PREVIEW_FLEET = deriveRowContext(
  PREVIEW_SESSIONS, { 'preview-worktree': 2 }, NOW, undefined, undefined,
  // Read aloud on, with the global default off, so the preview shows exactly what an
  // opted-in session looks like rather than marking every row.
  { enabled: true, default_mode: 'off' },
)

const SHAPES: Array<{ id: DotShape; label: string }> = [
  { id: 'hexagon', get label() { return t("SessionRowSettings.ddcc1ac4a5") } }, { id: 'circle', get label() { return t("SessionRowSettings.b93d3bcecf") } }, { id: 'square', get label() { return t("SessionRowSettings.c11092bc08") } },
]
const SIZE_PROFILES: Array<{ id: SettingsProfile; label: string }> = [
  { id: 'desktop', get label() { return t("SessionRowSettings.a6a067ba7c") } }, { id: 'mobile', get label() { return t("SessionRowSettings.d609da24ce") } },
]
const sizeKey = (profile: SettingsProfile): 'dotSizeDesktop' | 'dotSizeMobile' =>
  profile === 'mobile' ? 'dotSizeMobile' : 'dotSizeDesktop'
/** How long a continuous control must rest before its value is persisted. */
const SETTLE_MS = 250

/** Shared with the top-bar editor, so the two surfaces name one choice one way. */
export const CWD_STYLE_OPTIONS: Array<{ value: CwdStyle; label: string }> = [
  { value: 'leaf', get label() { return t("SessionRowSettings.14d34edf50") } },
  { value: 'relative', get label() { return t("SessionRowSettings.39e06f1225") } },
  { value: 'full', get label() { return t("SessionRowSettings.ff5bec3a8e") } },
]

const CONTEXT_MODES: Array<{ id: ContextRender; label: string; hint: string }> = [
  { id: 'arc', get label() { return t("SessionRowSettings.b52d7c4843") }, get hint() { return t("SessionRowSettings.d2e6939a34") } },
  { id: 'gauge', get label() { return t("SessionRowSettings.f6d8f4c86c") }, get hint() { return t("SessionRowSettings.2c0426c873") } },
  { id: 'percent', get label() { return t("SessionRowSettings.379db318f5") }, get hint() { return t("SessionRowSettings.6f4c3d7fc9") } },
  { id: 'both', get label() { return t("SessionRowSettings.0846db3956") }, get hint() { return t("SessionRowSettings.f08eea084e") } },
  { id: 'off', get label() { return t("SessionRowSettings.ca7981b46e") }, get hint() { return t("SessionRowSettings.d725471ef5") } },
]

/**
 * The three thresholds, in ramp order, labelled by the colour each one turns on.
 *
 * Named after the colour rather than after the band ("Warn at") because the
 * control sits beside a live preview: what the reader is matching the number to
 * is the shade they can see, not a word from the type system.
 */
const CONTEXT_BANDS: Array<{
  key: 'contextWarn' | 'contextHigh' | 'contextCrit'; label: string
}> = [
  { key: 'contextWarn', get label() { return t("SessionRowSettings.9287218c00") } },
  { key: 'contextHigh', get label() { return t("SessionRowSettings.93ea627d76") } },
  { key: 'contextCrit', get label() { return t("SessionRowSettings.1f0ccb6dc9") } },
]

const STANDING_MODES: Array<{ id: StandingRender; label: string; hint: string }> = [
  { id: 'row', get label() { return t("SessionRowSettings.cb260f4b27") }, get hint() { return t("SessionRowSettings.cdb20e9607") } },
  { id: 'indicator', get label() { return t("SessionRowSettings.877d69ffee") }, get hint() { return t("SessionRowSettings.5654240a4b") } },
  { id: 'off', get label() { return t("SessionRowSettings.ca7981b46e") }, get hint() { return t("SessionRowSettings.cf9998e49c") } },
]

export function SessionRowSettings() {
  const [config, setConfig] = useState(loadSessionRowConfig)
  const [error, setError] = useState('')
  const [previewExpanded, setPreviewExpanded] = useState(false)
  const thisDevice = currentProfile()
  // Desktop previews use this browser's persisted sidebar width. Mobile CSS uses
  // the navigation drawer's viewport-derived width. There is no second preview
  // geometry for the operator to configure.
  const previewWidth = storedSidebarWidth(localStorage.getItem(SIDEBAR_WIDTH_KEY))
  const previewMetricRef = useRef<HTMLDivElement>(null)
  const previewBudget = useRowBudget(previewMetricRef)
  const previewContext = useMemo(
    () => ({ ...PREVIEW_FLEET, budget: previewBudget }), [previewBudget, uiLocale.value, catalogRevision.value])

  useEffect(() => {
    const sync = () => setConfig(loadSessionRowConfig())
    window.addEventListener('mux:settings-changed', sync)
    return () => window.removeEventListener('mux:settings-changed', sync)
  }, [])

  const save = (next: SessionRowConfig) => {
    saveSessionRowConfig(next)
      .then(() => setError(''))
      .catch(() => setError(t("SessionRowSettings.9bea9c5c54")))
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
      <Dropdown
        ariaLabel={t("SessionRowSettings.e1025b7b12", { value1: descriptor.label })}
        title={t("SessionRowSettings.66fc791f7c", { notable: descriptor.notable })}
        value={mode}
        onChange={value => change(setFieldMode(config, id, value === 'always' ? 'always' : 'notable'))}
        options={[{ value: 'notable', get label() { return t("SessionRowSettings.ea44c278ae") } }, { value: 'always', get label() { return t("SessionRowSettings.9cdc6c47aa") } }]}
      />
      <span class="row-slot-actions">
        <button type="button" title={t("SessionRowSettings.736612d4d0")} disabled={index === 0} onClick={() => move(line, align, id, -1)}>↑</button>
        <button type="button" title={t("SessionRowSettings.d6e856081e")} disabled={index === total - 1} onClick={() => move(line, align, id, 1)}>↓</button>
        <button type="button" title={t("SessionRowSettings.c384d24b55", { other: other })} onClick={() => change(placeField(config, id, line, other))}>{align === 'left' ? '→' : '←'}</button>
        <button type="button" class="danger" title={t("SessionRowSettings.1bc9326fae")} disabled={id === 'title'} onClick={() => change(removeField(config, id))}>×</button>
      </span>
    </li>
  }

  const section = (line: RowLine, align: RowAlign) => {
    const slots = lineConfig(config, line)[align]
    return <div class="row-section-editor">
      <h5>{align === 'left' ? t("SessionRowSettings.58eb9032e3") : t("SessionRowSettings.883361d5d6")}</h5>
      {slots.length ? <ul>{slots.map((slot, index) => slotRow(line, align, slot.id, index, slots.length))}</ul> : <p class="row-section-empty">{t("SessionRowSettings.9124e73399")}</p>}
    </div>
  }

  const lineEditor = (line: RowLine, heading: string, note: string) => {
    const source = lineConfig(config, line)
    const available = unplacedFields(config).filter(field => Boolean(field.identity) === (line === 'top'))
    return <div class="row-line-editor">
      <h4>{heading}</h4>
      <p>{note}</p>
      <label>{rich("SessionRowSettings.7cfd5305e0", { value1: <Dropdown value={source.separator} onChange={value => change({
          ...config,
          [line]: { ...source, separator: value as SeparatorId },
        })} options={SEPARATOR_IDS.map(id => ({ value: id, label: SEPARATORS[id].label }))}/> })}</label>
      <div class="row-section-grid">{section(line, 'left')}{section(line, 'right')}</div>
      {available.length > 0 && <div class="row-field-pool">
        <span>{t("SessionRowSettings.f552a1e79b")}</span>
        {available.map(field => <button
          key={field.id}
          type="button"
          title={t("SessionRowSettings.66fc791f7c", { notable: field.notable })}
          onClick={() => change(placeField(config, field.id, line, 'left'))}
        >{field.label}</button>)}
      </div>}
    </div>
  }

  return <div class="session-row-settings">
    <h3 data-setting="session_rows" data-heading-message="heading.30921545c4">{t("heading.30921545c4")}</h3>
    <div class="session-row-preview-sticky">
      <div class="session-row-preview-heading">
        <div><strong>{t("SessionRowSettings.f65ddc1ff3")}</strong><small>{previewExpanded ? t("SessionRowSettings.835e885897") : t("SessionRowSettings.3cfe6603ab")}</small></div>
        <button type="button" aria-expanded={previewExpanded} aria-controls="session-row-preview-list" onClick={()=>setPreviewExpanded(value=>!value)}>
          {previewExpanded ? t("SessionRowSettings.08ad2dc884") : t("SessionRowSettings.fec71e7d5b")}
        </button>
      </div>
      <div
        class="session-row-preview"
        style={{
          '--session-dot': `${config[sizeKey(thisDevice)]}px`,
          '--session-row-preview-width': `${previewWidth}px`,
        }}
      >
        <div id="session-row-preview-list" class="session-list">
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
          {PREVIEW_SESSIONS.slice(0,previewExpanded?PREVIEW_SESSIONS.length:1).map(session => <div key={session.id} class={`session-row agent ${session.state}`}>
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
    </div>

    <p>{rich("SessionRowSettings.4c3bf8ddc6", { value1: <strong>{t("SessionRowSettings.f4a13765bd")}</strong> })}</p>
    {error && <p class="settings-inline-error" aria-live="polite">{error}</p>}

    <div class="theme-actions">
      {ROW_PRESETS.map(preset => <button
        key={preset.id}
        type="button"
        title={preset.description}
        onClick={() => change(presetConfig(preset.id as RowPresetId))}
      >{preset.label}</button>)}
      <button type="button" onClick={() => change(defaultSessionRowConfig())}>{t("SessionRowSettings.bc5b45ae7b")}</button>
    </div>

    <h4 data-heading-message="SessionRowSettings.1fff319f0d">{t("SessionRowSettings.1fff319f0d")}</h4>
    <label>{rich("SessionRowSettings.a8a079fb83", { value1: <Dropdown value={config.dotShape} onChange={value => change({ ...config, dotShape: value as DotShape })}
        options={SHAPES.map(shape => ({ value: shape.id, label: shape.label }))}/> })}</label>
    {SIZE_PROFILES.map(profile => <label key={profile.id} class="row-size-control">
      <span>
        {profile.label}
        {profile.id === thisDevice ? t("SessionRowSettings.41de3fbdea") : ''}
      </span>
      <input
        type="range"
        min={DOT_SIZE_MIN}
        max={DOT_SIZE_MAX}
        step={1}
        value={config[sizeKey(profile.id)]}
        aria-label={t("SessionRowSettings.5758f3b382", { label: profile.label })}
        onInput={event => {
          changeContinuous({
            ...config,
            [sizeKey(profile.id)]:
              normalizeDotSize(event.currentTarget.valueAsNumber, config[sizeKey(profile.id)]),
          })
        }}
      />
      <output>{rich("SessionRowSettings.6ae65e0651", { value1: config[sizeKey(profile.id)] })}</output>
    </label>)}
    <p>{t("SessionRowSettings.acd0de08f9")}</p>
    <label>{rich("SessionRowSettings.fbf2b0b705", { value1: <Dropdown value={config.context} onChange={value => change({ ...config, context: value as ContextRender })}
        options={CONTEXT_MODES.map(mode => ({ value: mode.id, label: mode.label }))}/> })}</label>
    <p>{rich("SessionRowSettings.1ea87e3bde", { hint: CONTEXT_MODES.find(mode => mode.id === config.context)?.hint, value2: <em>{t("SessionRowSettings.4ba69735ca")}</em> })}</p>
    {config.context !== 'off' && <>
      {CONTEXT_BANDS.map(band => <label key={band.key} class="row-size-control">
        <span>{band.label}</span>
        <input
          type="range"
          min={1}
          max={99}
          step={1}
          value={Math.round(config[band.key] * 100)}
          aria-label={t("SessionRowSettings.84faf4f14b", { label: band.label })}
          onInput={event => changeContinuous(
            // Through the shared normalizer rather than clamped here, so a
            // threshold dragged past its neighbour resolves the same way it
            // would if the blob arrived from another device already crossed.
            normalizeSessionRowConfig({
              ...config, [band.key]: event.currentTarget.valueAsNumber / 100,
            }),
          )}
        />
        <output>{Math.round(config[band.key] * 100)}%</output>
      </label>)}
      <p>{t("SessionRowSettings.d6de57c6f4")}</p>
    </>}
    <label>{rich("SessionRowSettings.43d2867a76", { value1: <Dropdown value={config.standing} onChange={value => change({ ...config, standing: value as StandingRender })}
        options={STANDING_MODES.map(mode => ({ value: mode.id, label: mode.label }))}/> })}</label>
    <p>{rich("SessionRowSettings.239c1c7130", { hint: STANDING_MODES.find(mode => mode.id === config.standing)?.hint })}</p>

    {lineEditor('top', t("SessionRowSettings.869730606d"), t("SessionRowSettings.f593914f97"))}
    {lineEditor('bottom', t("SessionRowSettings.d84a88be68"), t("SessionRowSettings.297ed2cea2"))}

    <h4 data-heading-message="SessionRowSettings.3066923729">{t("SessionRowSettings.3066923729")}</h4>
    <p>{rich("SessionRowSettings.8bb46eaaab", { value1: <em>{t("SessionRowSettings.c7761e5896")}</em>, value2: <code>{t("SessionRowSettings.e62b04aadf")}</code> })}</p>
    <p>{rich("SessionRowSettings.af422dbbbb", { value1: <strong>{t("SessionRowSettings.d8194812c0")}</strong>, value2: <strong>{t("SessionRowSettings.52656e8104")}</strong> })}</p>

    <h4 data-heading-message="SessionRowSettings.18807601d1">{t("SessionRowSettings.18807601d1")}</h4>
    <label>{rich("SessionRowSettings.207a8bcac4", { value1: <Dropdown value={config.diffStyle} onChange={value => change({ ...config, diffStyle: value as DiffStyle })} options={[
        { value: 'numbers', get label() { return t("SessionRowSettings.fab4489695") } },
        { value: 'bar', get label() { return t("SessionRowSettings.30f9c04830") } },
      ]}/> })}</label>
    <label>{rich("SessionRowSettings.7e0931c6f4", { value1: <Dropdown value={config.countStyle} onChange={value => change({ ...config, countStyle: value as CountStyle })} options={[
        { value: 'numbers', get label() { return t("SessionRowSettings.b0640f0fc4") } },
        { value: 'pips', get label() { return t("SessionRowSettings.9e74278ca4") } },
      ]}/> })}</label>
    <label>{rich("SessionRowSettings.5ed1b842bf", { value1: <Dropdown value={config.cwdStyle} onChange={value => change({ ...config, cwdStyle: value as CwdStyle })} options={CWD_STYLE_OPTIONS}/> })}</label>
    <label class="check"><span>{t("SessionRowSettings.1d4d684c3f")}</span>
      <input type="checkbox" checked={config.gitGlyphs} onChange={event => change({ ...config, gitGlyphs: event.currentTarget.checked })} />
    </label>

    <h4 data-heading-message="SessionRowSettings.cbaad3cf4a">{t("SessionRowSettings.cbaad3cf4a")}</h4>
    <label class="check"><span>{t("SessionRowSettings.bf9427ed33")}</span>
      <input type="checkbox" checked={config.mobileFields} onChange={event => change({ ...config, mobileFields: event.currentTarget.checked })} />
    </label>
    <p>{t("SessionRowSettings.8521101f77")}</p>

    <p>{rich("SessionRowSettings.b2afc660f1", { value1: ROW_FIELDS.map(field => field.label).join(', ') })}</p>
  </div>
}
