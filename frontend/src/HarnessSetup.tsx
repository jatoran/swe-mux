import { useModalFocus } from './modalFocus'
import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { Dropdown } from './Dropdown.tsx'
import { hostQuery } from './hostProfile.ts'
import { ThemePicker } from './ThemePicker.tsx'
import type { SetupDraft } from './onboarding'
import { SetupAccounts } from './SetupAccounts'
import { ExperiencePreview } from './ExperiencePreview'
import { applyTheme, type CustomTheme, type ThemeName } from './theme'
import {
  allHarnessesIncludingDisabled,
  installHarnessRegistry,
  setHarnessEnablement,
  type HarnessRegistryPayload,
} from './harnessRegistry'

/** Shared experience and harness pages. OnboardingFlow owns persistence and sequencing. */

export type ExperienceTier = 'terminal' | 'deterministic' | 'automations'

export const EXPERIENCE_TIERS: { id: ExperienceTier; title: string; blurb: string }[] = [
  {
    id: 'terminal',
    title: 'Pure terminal',
    blurb: 'Agent and shell terminals without hooks, status tracking, or fleet tools. '
      + 'Add individual features whenever you need them.',
  },
  {
    id: 'deterministic',
    title: 'Deterministic',
    blurb: 'Transcripts, live status, project memory, managed harnesses, and fleet tools. '
      + 'These features use no model API.',
  },
  {
    id: 'automations',
    title: 'Automations',
    blurb: 'Everything in Deterministic, plus the scan timeline and model-backed observers. '
      + 'Next, set up a provider, approve the models, and choose spending limits.',
  },
]

export type AutonomyLevel = 'supervised' | 'assisted' | 'autonomous'

/** Labels only. The values each level assigns live on the daemon
 *  (`experience_tiers.py`) and are applied by name, never recomputed here. */
export const AUTONOMY_LEVELS: { id: AutonomyLevel; title: string; blurb: string }[] = [
  {
    id: 'supervised',
    title: 'Supervised',
    blurb: 'Nothing is delivered to an agent without you pressing send.',
  },
  {
    id: 'assisted',
    title: 'Assisted',
    blurb: 'Queued messages deliver themselves when a session is idle and safe, under the shipped caps.',
  },
  {
    id: 'autonomous',
    title: 'Autonomous',
    blurb: 'Built for long multi-agent runs: auto-delivery with wider caps - more consecutive '
      + 'sends, a longer idle grant, twice the spawn budget. Every stability gate still applies.',
  },
]

/** How agents in every harness reach the fleet surface, applied install-wide on the
 *  agents page. `default` writes nothing: MCP and the CLI are on and the skill is off
 *  for every harness with no entry, so the recommended answer stays the empty map. */
export type FleetAccessChoice = 'default' | 'mcp' | 'cli' | 'none'

export const FLEET_ACCESS_CHOICES: { id: FleetAccessChoice; label: string; note: string }[] = [
  {
    id: 'default',
    label: 'MCP tools + agent CLI (recommended)',
    note: 'Agents see the fleet through self-describing MCP tools, with the same tools available as a CLI.',
  },
  {
    id: 'mcp',
    label: 'MCP tools only',
    note: 'The usual surface alone; the `swemux agent` CLI transport stays off.',
  },
  {
    id: 'cli',
    label: 'Agent CLI + skill file',
    note: 'No MCP registration. The skill file is delivered so agents learn the CLI exists - it does not advertise itself.',
  },
  {
    id: 'none',
    label: 'No fleet access',
    note: 'Sessions hold no fleet surface at all; the daemon refuses their tokens.',
  },
]

/** Per-harness dictionaries the fleet-access choice writes, over the named harnesses.
 *  Exported for the unit test: the mapping is the contract, the JSX only renders it. */
export function fleetAccessChanges(
  choice: FleetAccessChoice,
  harnesses: readonly string[],
): Record<string, Record<string, boolean>> {
  if (choice === 'default') return {}
  const all = (value: boolean) => Object.fromEntries(harnesses.map(name => [name, value]))
  if (choice === 'mcp') return { harness_cli_enabled: all(false) }
  if (choice === 'cli') return { harness_mcp_enabled: all(false), harness_skill_enabled: all(true) }
  return { harness_mcp_enabled: all(false), harness_cli_enabled: all(false) }
}

/** Just enough of a preset for the first-run line; Settings reads the full shape. */
export type KeymapPreset = { id: string; title: string; description: string; warning: string }

/** What a fresh install has before anyone chooses, mirroring `keymaps.DEFAULT_PRESET`. */
export const DEFAULT_KEYMAP_PRESET = 'swemux'

/** What a fresh install has before anyone chooses, mirroring `config.theme`. */
export const DEFAULT_THEME: ThemeName = 'tokyo-night'

/**
 * The one sentence under the picker: what the highlighted preset costs, or what
 * the default is, in the reader's terms.
 *
 * The warning is shown *here* rather than only in Settings because it is the
 * decision it belongs to: choosing "tmux" takes Ctrl+B away from any tmux running
 * inside a pane, and finding that out afterwards is the worst possible time.
 */
export function keymapNote(presets: KeymapPreset[], selected: string): string {
  const preset = presets.find(entry => entry.id === selected)
  if (!preset) return 'Pick the shortcuts you already know. Everything stays editable in Settings.'
  if (preset.warning) return preset.warning
  return preset.description
}

/** The daemon's tier/autonomy assignments, for the Customize fold-out to draw from. */
type TierAssignments = {
  tiers: Record<string, Record<string, unknown>>
  autonomy: Record<string, Record<string, unknown>>
  overridable: string[]
}

/**
 * Reading order and copy for the overridable switches. Keys the daemon serves
 * that this map predates still render, under their raw name, so a new tier key
 * appears here at worst unstyled rather than silently missing.
 */
const OVERRIDE_COPY: Record<string, { label: string; hint: string }> = {
  agent_shims_on_shell_path: { label: 'Agent shims on the shell PATH', hint: 'Agents launched by hand inside a shell session still report in.' },
  agent_messaging_enabled: { label: 'Agent-to-agent messages', hint: 'Sessions can stage messages to each other through the prompt queue.' },
  agent_interject_enabled: { label: 'Mid-turn interjections', hint: 'A queued message may be delivered while a turn is still running.' },
  session_control_enabled: { label: 'Session control', hint: 'Agents with the authority can interrupt or end other sessions.' },
  request_spawn_enabled: { label: 'Spawn requests', hint: 'Agents can draft new-session requests for you to approve.' },
  session_watch_enabled: { label: 'Session watches', hint: 'An agent can ask to be told once when another session settles.' },
  scheduled_runs_enabled: { label: 'Scheduled runs', hint: 'Sessions this machine starts on its own schedule.' },
  land_queue_enabled: { label: 'Land queue', hint: 'Reconcile, verify, and fast-forward finished branches one at a time.' },
  automation_enabled: { label: 'Automation rules', hint: 'The model-backed pipeline; per-Project opt-ins and budgets still apply.' },
  scan_timeline_enabled: { label: 'Scan timeline', hint: 'Periodic model reads of session activity, under its own budget.' },
}

const OVERRIDE_ORDER = Object.keys(OVERRIDE_COPY)

export function HarnessSetup({page,workflow}:{
  page:'experience'|'harnesses'
  workflow:{draft:SetupDraft;onDraft:(draft:SetupDraft)=>Promise<void>;onTier:(draft:SetupDraft)=>Promise<void>;onHarnesses:(draft:SetupDraft)=>Promise<void>;onDefer:(draft:SetupDraft)=>Promise<void>}
}) {
  const step=page==='experience'?'tier':'harnesses'
  const [tier, setTier] = useState<ExperienceTier>(workflow?.draft.tier||'deterministic')
  const appliedTier=workflow.draft.tier||''
  // The keyboard preset, on the same page as the tier. Fetched rather than listed,
  // because the preset table is data on the daemon (`assets/keymaps/`) and a copy
  // here would be a second one to keep in step.
  const [presets, setPresets] = useState<KeymapPreset[]>([])
  const [keymap, setKeymap] = useState(workflow?.draft.keymap||DEFAULT_KEYMAP_PRESET)
  // The theme, previewed live so the catalogue can be walked and seen. `initialTheme`
  // is what the daemon holds now, and is what a skip or an unchanged Continue leaves
  // applied; only a Continue with a different choice writes it.
  const [theme, setTheme] = useState<ThemeName>(DEFAULT_THEME)
  const [initialTheme, setInitialTheme] = useState<ThemeName>(DEFAULT_THEME)
  const [customTheme, setCustomTheme] = useState<CustomTheme | undefined>(undefined)
  const [themeOpen, setThemeOpen] = useState(false)
  // The Customize fold-out: daemon-served assignments, the autonomy level, and the
  // user's explicit switch deviations. Overrides are cleared when the tier changes,
  // because a deviation is a statement about one tier's defaults.
  const [assignments, setAssignments] = useState<TierAssignments | null>(null)
  const [customizeOpen, setCustomizeOpen] = useState(false)
  const [autonomy, setAutonomy] = useState<AutonomyLevel>((workflow?.draft.autonomy as AutonomyLevel)||'supervised')
  const [overrides, setOverrides] = useState<Record<string, boolean>>(workflow?.draft.overrides||{})
  const [railDesktop, setRailDesktop] = useState(workflow?.draft.rail_desktop??true)
  const [railMobile, setRailMobile] = useState(workflow?.draft.rail_mobile??true)
  const [fleetAccess, setFleetAccess] = useState<FleetAccessChoice>((workflow?.draft.fleet_access as FleetAccessChoice)||'default')
  const [defaultHarness,setDefaultHarness]=useState(workflow?.draft.default_harness||'')
  const [choices, setChoices] = useState<Record<string, boolean>>({})
  const [scanHistory, setScanHistory] = useState(workflow?.draft.scan_history??true)
  const [ready, setReady] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    api<HarnessRegistryPayload>('GET', '/api/harnesses').then(payload => {
      if (!live) return
      installHarnessRegistry(payload)
      const initial: Record<string, boolean> = {}
      for (const harness of payload.harnesses) initial[harness.name] = !!harness.installed
      setChoices(workflow?.draft.harnesses||initial)
      if(!workflow?.draft.default_harness)setDefaultHarness(payload.harnesses.find(harness=>harness.installed)?.name||'')
      setReady(true)
    }).catch(cause => { if (live) setError(`Harness detection failed: ${cause.message}. Close setup and resume to retry.`) })
    api<{ presets: KeymapPreset[] }>('GET', `/api/keybindings?${hostQuery()}`)
      .then(payload => { if (live) setPresets(payload.presets || []) })
      .catch(() => { /* the picker simply does not appear; the default preset stands */ })
    api<{ theme?: ThemeName; custom_theme?: CustomTheme }>('GET', '/api/config')
      .then(payload => {
        if (!live || !payload.theme) return
        setTheme((workflow?.draft.theme as ThemeName)||payload.theme)
        setInitialTheme(payload.theme)
        setCustomTheme(payload.custom_theme)
      })
      .catch(() => { /* the picker starts from the shipped default */ })
    api<TierAssignments>('GET', '/api/experience-tiers')
      .then(payload => { if (live) setAssignments(payload) })
      .catch(() => { /* the Customize fold-out simply does not appear */ })
    return () => { live = false }
  }, [])

  const detected = allHarnessesIncludingDisabled()
  const draftSnapshot=():SetupDraft=>({tier,autonomy,overrides,theme,keymap,fleet_access:fleetAccess,harnesses:choices,default_harness:defaultHarness,scan_history:scanHistory,rail_desktop:railDesktop,rail_mobile:railMobile})
  useEffect(()=>{
    if(!workflow||!ready)return
    const timer=setTimeout(()=>void workflow.onDraft(draftSnapshot()).catch(cause=>setError(cause.message)),400)
    return()=>clearTimeout(timer)
  },[ready,tier,autonomy,JSON.stringify(overrides),theme,keymap,fleetAccess,JSON.stringify(choices),defaultHarness,scanHistory,railDesktop,railMobile])

  const chooseTheme = (value: ThemeName) => {
    setTheme(value)
    applyTheme(value)
  }

  const chooseTier = (value: ExperienceTier) => {
    setTier(value)
    // A deviation is relative to one tier's defaults; keeping it across a tier
    // change would silently mean something else.
    setOverrides({})
  }

  /** The switch keys to draw, in curated order, with daemon-served strays appended. */
  const overrideKeys = (): string[] => {
    if (!assignments) return []
    const served = new Set(assignments.overridable)
    return [
      ...OVERRIDE_ORDER.filter(key => served.has(key)),
      ...assignments.overridable.filter(key => !(key in OVERRIDE_COPY)),
    ]
  }

  const tierValue = (key: string): boolean => assignments?.tiers[tier]?.[key] === true

  const effectiveValue = (key: string): boolean => overrides[key] ?? tierValue(key)

  const toggleOverride = (key: string, value: boolean) => {
    setOverrides(current => {
      const next = { ...current }
      if (value === tierValue(key)) delete next[key]
      else next[key] = value
      return next
    })
  }

  const applyTier = async () => {
    setBusy(true);setError('')
    try{await workflow.onTier(draftSnapshot())}
    catch(cause){setError((cause as Error).message);setBusy(false)}
  }
  const enable = async () => {
    setBusy(true);setError('')
    try{
      const selected=detected.filter(harness=>choices[harness.name])
      if(selected.length&&!selected.some(harness=>harness.name===defaultHarness))throw new Error('Choose an enabled default harness.')
      await workflow.onHarnesses(draftSnapshot())
    }catch(cause){setError((cause as Error).message);setBusy(false)}
  }
  const skip = async () => {
    setBusy(true)
    try{applyTheme(initialTheme);await workflow.onDefer(draftSnapshot())}
    catch(cause){setError((cause as Error).message);setBusy(false)}
  }

  const dialog=useRef<HTMLElement>(null)
  useModalFocus(dialog,()=>void skip(),!busy,'setup')

  const anyDetected = detected.some(harness => harness.installed)

  if (step === 'tier') {
    return <div class="harness-setup-backdrop" role="dialog" aria-modal="true" aria-label="Choose how much swe-mux does">
      <section ref={dialog} class="harness-setup">
        <header><strong>SET UP::EXPERIENCE</strong></header>
        <div class="harness-setup-body">
          <p>How much should swe-mux do? Each tier is a set of defaults, not a lock: everything a tier leaves off stays one switch away in Settings, and you can re-apply a different tier there any time.</p>
          {EXPERIENCE_TIERS.map(entry => <label class="harness-setup-row check harness-setup-tier" key={entry.id}>
            <span>
              <strong>{entry.title}</strong>
              <small>{entry.blurb}</small>
            </span>
            <input type="radio" name="experience-tier" checked={tier === entry.id} onChange={() => chooseTier(entry.id)} />
          </label>)}
          <ExperiencePreview tier={tier}/>
          {/* The Customize fold-out. Drawn only when the daemon served the assignments,
              because the switches are seeded from the chosen tier's own key set - a
              hand-written seed here would be the second copy of the policy. */}
          {assignments && <div class="harness-setup-customize">
            <button type="button" class="link" aria-expanded={customizeOpen} onClick={() => setCustomizeOpen(open => !open)}>
              {customizeOpen ? '▾' : '▸'} Customize what {EXPERIENCE_TIERS.find(entry => entry.id === tier)?.title ?? tier} sets
            </button>
            {customizeOpen && <div class="harness-setup-customize-body">
              {tier !== 'terminal' && <div class="harness-setup-autonomy" role="radiogroup" aria-label="Agent autonomy">
                <strong>Agent autonomy</strong>
                <small>How much agents may do without you pressing send. Separate from the tier, and just as reversible.</small>
                {AUTONOMY_LEVELS.map(level => <label class="harness-setup-row check" key={level.id}>
                  <span><strong>{level.title}</strong><small>{level.blurb}</small></span>
                  <input type="radio" name="autonomy-level" checked={autonomy === level.id} onChange={() => setAutonomy(level.id)} />
                </label>)}
              </div>}
              <div class="harness-setup-overrides">
                <strong>Individual switches</strong>
                <small>Seeded from the tier; change any of them and the deviation is applied with it. Switching tiers resets these.</small>
                {overrideKeys().map(key => <label class="harness-setup-row check harness-setup-override" key={key}>
                  <span>
                    <strong>{OVERRIDE_COPY[key]?.label ?? key}</strong>
                    {OVERRIDE_COPY[key] && <small>{OVERRIDE_COPY[key].hint}</small>}
                  </span>
                  <input type="checkbox" checked={effectiveValue(key)} onChange={event => toggleOverride(key, event.currentTarget.checked)} />
                </label>)}
                <label class="harness-setup-row check harness-setup-override">
                  <span><strong>Command rail (desktop)</strong><small>The row of keys and actions under each terminal.</small></span>
                  <input type="checkbox" checked={railDesktop} onChange={event => setRailDesktop(event.currentTarget.checked)} />
                </label>
                <label class="harness-setup-row check harness-setup-override">
                  <span><strong>Command rail (mobile)</strong><small>On a phone the rail is the keyboard; most people keep it.</small></span>
                  <input type="checkbox" checked={railMobile} onChange={event => setRailMobile(event.currentTarget.checked)} />
                </label>
              </div>
            </div>}
          </div>}
          <label class="harness-setup-row harness-setup-theme">
            <span>
              <strong>Theme</strong>
              <small>Previewed as you browse; nothing is written until Continue. Custom palettes live in Settings → Appearance.</small>
            </span>
            <ThemePicker
              value={theme}
              customTheme={customTheme ?? { background: '#090a0c', panel: '#0d0f12', line: '#2a2e34', foreground: '#d9dde2', muted: '#848b94', accent: '#8bd450', error: '#f07178' }}
              open={themeOpen}
              onOpenChange={setThemeOpen}
              onChange={chooseTheme}
              onPreview={value => applyTheme(value ?? theme)}
            />
          </label>
          {/* One line, on the page that already exists, rather than a fourth first-run
              surface. `firstRunSurface()` arbitrates three and the tier step was
              folded in here for the same reason; a keymap is a defaults choice of
              exactly the same shape, and it is reversible from Settings either way.
              Left at the default when the picker cannot be drawn: a preset nobody
              chose is the default preset, which is what a fresh install already has. */}
          {!!presets.length && <label class="harness-setup-row harness-setup-keymap">
            <span>
              <strong>Coming from another tool?</strong>
              <small>{keymapNote(presets, keymap)}</small>
            </span>
            <Dropdown
              ariaLabel="Keyboard shortcut preset"
              value={keymap}
              onChange={value => setKeymap(value)}
              options={presets.map(preset => ({ value: preset.id, label: preset.title }))}
            />
          </label>}
          {error && <p class="harness-setup-error" role="alert">{error}</p>}
        </div>
        <footer>
          <span class="harness-setup-spacer" />
          <button type="button" disabled={busy} onClick={() => void skip()}>Continue later</button>
          <button type="button" class="primary" disabled={busy} onClick={() => void applyTier()}>Continue</button>
        </footer>
      </section>
    </div>
  }

  return <div class="harness-setup-backdrop" role="dialog" aria-modal="true" aria-label="Set up agents">
    <section ref={dialog} class="harness-setup">
      <header><strong>SET UP::AGENTS</strong></header>
      <div class="harness-setup-body">
        <p>swe-mux found these agent CLIs on this machine. Enabled harnesses appear in the launchers; you can change any of this later under Settings → Agents.</p>
        {appliedTier==='terminal'
          ? <p class="harness-setup-note">Your pure-terminal choice is saved and applies fully at the next daemon reload (menu → Reload daemon; sessions survive). Until then, sessions launch with the standard instrumentation.</p>
          : <p class="harness-setup-note">When mux launches an enabled agent it adds two things per session: its lifecycle hooks (so status, history, and the prompt queue work) and a read-only mux MCP server (so the agent can see the fleet). Both are per-session and removed when the session ends. You can turn either off per harness under Settings → Agents ("launch clean" runs an agent unobserved).</p>}
        <p class="harness-setup-note">Next, choose project folders from your session history or add a folder yourself. Your existing harness login can be saved here without signing in again.</p>
        {!ready&&<p class="harness-setup-loading">Detecting…</p>}
        {ready&&!detected.length&&<p>No harnesses are registered.</p>}
        {ready&&detected.map(harness=><label class="harness-setup-row check" key={harness.name}>
          <span>
            <strong>{harness.display_name}</strong>
            <small class={harness.installed?'harness-setup-found':'harness-setup-absent'}>{harness.installed?(harness.resolved_path?`Detected: ${harness.resolved_path}`:'Detected (data present)'):'Not detected'}</small>
          </span>
          <input type="checkbox" checked={choices[harness.name]??false} onChange={e=>{const next={...choices,[harness.name]:e.currentTarget.checked};setChoices(next);if(!next[defaultHarness])setDefaultHarness(detected.find(item=>next[item.name])?.name||'')}} />
        </label>)}
        {workflow&&ready&&<>
          <label class="harness-setup-row"><span><strong>Default harness for Run</strong><small>You can choose a different harness for any session.</small></span><Dropdown ariaLabel="Default harness" value={defaultHarness} onChange={setDefaultHarness} options={detected.filter(harness=>choices[harness.name]).map(harness=>({value:harness.name,label:harness.display_name}))}/></label>
          <SetupAccounts enabled={detected.filter(harness=>choices[harness.name]).map(harness=>harness.name)}/>
        </>}
        {/* Install-wide rather than per-harness on purpose: the three per-harness
            checkboxes live in Settings → Agents, and first run wants the one question
            most people mean ("how do agents reach the fleet"), answered once. Hidden
            when the terminal tier was applied, because that tier already wrote every
            surface off. */}
        {ready&&!!detected.length&&appliedTier!=='terminal'&&<label class="harness-setup-row harness-setup-fleet">
          <span>
            <strong>Fleet access</strong>
            <small>{FLEET_ACCESS_CHOICES.find(entry=>entry.id===fleetAccess)?.note}</small>
          </span>
          <Dropdown
            ariaLabel="Fleet access"
            value={fleetAccess}
            onChange={value=>setFleetAccess(value as FleetAccessChoice)}
            options={FLEET_ACCESS_CHOICES.map(entry=>({value:entry.id,label:entry.label}))}
          />
        </label>}
        {ready&&anyDetected&&<label class="harness-setup-scan check"><span><strong>Scan history for these now</strong><small>Index past conversations the enabled CLIs wrote on their own. A large history can take a while; it runs in the background and can be cancelled from Settings → Agents.</small></span><input type="checkbox" checked={scanHistory} onChange={e=>setScanHistory(e.currentTarget.checked)} /></label>}
        {error&&<p class="harness-setup-error" role="alert">{error}</p>}
      </div>
      <footer>
        <span class="harness-setup-spacer" />
        <button type="button" disabled={busy} onClick={()=>void skip()}>Continue later</button>
        <button type="button" class="primary" disabled={busy||!ready} onClick={()=>void enable()}>Enable selected</button>
      </footer>
    </section>
  </div>
}
