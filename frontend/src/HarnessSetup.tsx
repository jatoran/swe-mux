import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import {
  allHarnessesIncludingDisabled,
  installHarnessRegistry,
  setHarnessEnablement,
  type HarnessRegistryPayload,
} from './harnessRegistry'

/**
 * First-run panel: the experience tier, then the harness list.
 *
 * The tier step comes first because it frames everything after it, and it is
 * phrased as three genuine products rather than a good/reduced ladder - pure
 * terminal is the strongest claim swe-mux has against tools that re-render
 * agents into their own UI, and a reader who concludes it is "the one without
 * the good features" has been failed by the copy. A tier sets defaults through
 * `POST /api/experience-tier` and locks nothing; every switch it touches stays
 * individually editable in Settings.
 *
 * The harness step runs detection, lists what it found with detected harnesses
 * pre-ticked, offers a separate "scan history" choice, and a skip that writes
 * nothing but the completion flag. First-run state is daemon-side
 * (`harness_setup_complete`, `experience_tier`), not device-local, because both
 * are machine config: a choice made on the desktop must not reappear on the
 * phone.
 *
 * `onConfigureMore` hands off to Settings -> Agents for per-harness executable,
 * arguments, and width editing, so this panel assembles the enable/scan parts
 * rather than duplicating that surface.
 */

export type ExperienceTier = 'terminal' | 'deterministic' | 'automations'

export const EXPERIENCE_TIERS: { id: ExperienceTier; title: string; blurb: string }[] = [
  {
    id: 'terminal',
    title: 'Pure terminal',
    blurb: 'Real terminals and nothing else. Agents run in genuine PTYs with no hooks, no '
      + 'status detection, and no fleet plumbing - swe-mux never touches what runs inside. '
      + 'The strongest choice when that guarantee is the point; everything else stays one '
      + 'switch away, never removed.',
  },
  {
    id: 'deterministic',
    title: 'Deterministic',
    blurb: 'Terminals plus the model-free layer: transcripts, live status detection, managed '
      + 'harnesses, and the agent fleet surface. Nothing here calls a model or spends money.',
  },
  {
    id: 'automations',
    title: 'Automations',
    blurb: 'Everything in Deterministic, plus the scan timeline and the model-backed '
      + 'observers - the parts that spend tokens, under budgets you set and per-Project '
      + 'switches you opt into.',
  },
]

export function HarnessSetup(
  { tierNeeded, onDone, onConfigureMore }:
  { tierNeeded: boolean; onDone: () => void; onConfigureMore: () => void },
) {
  const [step, setStep] = useState<'tier' | 'harnesses'>(tierNeeded ? 'tier' : 'harnesses')
  const [tier, setTier] = useState<ExperienceTier>('deterministic')
  const [tierRestart, setTierRestart] = useState(false)
  const [choices, setChoices] = useState<Record<string, boolean>>({})
  const [scanHistory, setScanHistory] = useState(true)
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
      setChoices(initial)
      setReady(true)
    }).catch(() => { if (live) setReady(true) })
    return () => { live = false }
  }, [])

  const detected = allHarnessesIncludingDisabled()

  const applyTier = async () => {
    setBusy(true)
    setError('')
    try {
      const applied = await api<{ restart_required: string[] }>(
        'POST', '/api/experience-tier', { tier },
      )
      setTierRestart(applied.restart_required.length > 0)
      setStep('harnesses')
      setBusy(false)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      setBusy(false)
    }
  }

  // Persist only choices that differ from detection, so the stored map stays the
  // three-state minimum: an installed harness left ticked follows detection (no
  // entry), only an override in either direction is written.
  const explicitChoices = (): Record<string, boolean> => {
    const explicit: Record<string, boolean> = {}
    for (const harness of detected) {
      const ticked = choices[harness.name] ?? false
      if (ticked !== !!harness.installed) explicit[harness.name] = ticked
    }
    return explicit
  }

  const enable = async () => {
    setBusy(true)
    setError('')
    try {
      const explicit = explicitChoices()
      await api('PATCH', '/api/config', { harness_enabled: explicit, harness_setup_complete: true })
      setHarnessEnablement(explicit)
      if (scanHistory) await api('POST', '/api/history/scan').catch(() => {})
      onDone()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      setBusy(false)
    }
  }

  // Skip writes nothing but the completion flag: no `harness_enabled` entries (a
  // harness installed later is still picked up by detection), and no tier - an
  // unmade choice stays visible as unmade in Settings rather than being guessed.
  const skip = async () => {
    setBusy(true)
    setError('')
    try {
      await api('PATCH', '/api/config', { harness_setup_complete: true })
      onDone()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      setBusy(false)
    }
  }

  const anyDetected = detected.some(harness => harness.installed)

  if (step === 'tier') {
    return <div class="harness-setup-backdrop" role="dialog" aria-modal="true" aria-label="Choose how much swe-mux does">
      <section class="harness-setup">
        <header><strong>SET UP::EXPERIENCE</strong></header>
        <div class="harness-setup-body">
          <p>How much should swe-mux do? Each tier is a set of defaults, not a lock: everything a tier leaves off stays one switch away in Settings, and you can re-apply a different tier there any time.</p>
          {EXPERIENCE_TIERS.map(entry => <label class="harness-setup-row check harness-setup-tier" key={entry.id}>
            <span>
              <strong>{entry.title}</strong>
              <small>{entry.blurb}</small>
            </span>
            <input type="radio" name="experience-tier" checked={tier === entry.id} onChange={() => setTier(entry.id)} />
          </label>)}
          {error && <p class="harness-setup-error" role="alert">{error}</p>}
        </div>
        <footer>
          <button type="button" class="link" disabled={busy} onClick={() => { void skip(); onConfigureMore() }}>Configure in Settings…</button>
          <span class="harness-setup-spacer" />
          <button type="button" disabled={busy} onClick={() => void skip()}>Skip setup</button>
          <button type="button" class="primary" disabled={busy} onClick={() => void applyTier()}>Continue</button>
        </footer>
      </section>
    </div>
  }

  return <div class="harness-setup-backdrop" role="dialog" aria-modal="true" aria-label="Set up agents">
    <section class="harness-setup">
      <header><strong>SET UP::AGENTS</strong></header>
      <div class="harness-setup-body">
        <p>swe-mux found these agent CLIs on this machine. Enabled harnesses appear in the launchers; you can change any of this later under Settings → Agents.</p>
        {tierRestart
          ? <p class="harness-setup-note">Your pure-terminal choice is saved and applies fully at the next daemon reload (menu → Reload daemon; sessions survive). Until then, sessions launch with the standard instrumentation.</p>
          : <p class="harness-setup-note">When mux launches an enabled agent it adds two things per session: its lifecycle hooks (so status, history, and the prompt queue work) and a read-only mux MCP server (so the agent can see the fleet). Both are per-session and removed when the session ends. You can turn either off per harness under Settings → Agents ("launch clean" runs an agent unobserved).</p>}
        <p class="harness-setup-note">Next, after this: create a Project for a folder, sign in to each agent CLI so its account and history appear (mux reads Claude and Codex auth, so the account switcher is empty until you run each CLI's login), then start a session. Set up a phone under Settings → Remote.</p>
        {!ready&&<p class="harness-setup-loading">Detecting…</p>}
        {ready&&!detected.length&&<p>No harnesses are registered.</p>}
        {ready&&detected.map(harness=><label class="harness-setup-row check" key={harness.name}>
          <span>
            <strong>{harness.display_name}</strong>
            <small class={harness.installed?'harness-setup-found':'harness-setup-absent'}>{harness.installed?(harness.resolved_path?`Detected: ${harness.resolved_path}`:'Detected (data present)'):'Not detected'}</small>
          </span>
          <input type="checkbox" checked={choices[harness.name]??false} onChange={e=>setChoices(current=>({...current,[harness.name]:e.currentTarget.checked}))} />
        </label>)}
        {ready&&anyDetected&&<label class="harness-setup-scan check"><span><strong>Scan history for these now</strong><small>Index past conversations the enabled CLIs wrote on their own. A large history can take a while; it runs in the background and can be cancelled from Settings → Agents.</small></span><input type="checkbox" checked={scanHistory} onChange={e=>setScanHistory(e.currentTarget.checked)} /></label>}
        {error&&<p class="harness-setup-error" role="alert">{error}</p>}
      </div>
      <footer>
        <button type="button" class="link" disabled={busy} onClick={()=>{ void skip(); onConfigureMore() }}>Configure in Settings…</button>
        <span class="harness-setup-spacer" />
        <button type="button" disabled={busy} onClick={()=>void skip()}>Skip</button>
        <button type="button" class="primary" disabled={busy||!ready} onClick={()=>void enable()}>Enable selected</button>
      </footer>
    </section>
  </div>
}
