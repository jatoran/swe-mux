import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import {
  allHarnessesIncludingDisabled,
  installHarnessRegistry,
  setHarnessEnablement,
  type HarnessRegistryPayload,
} from './harnessRegistry'

/**
 * First-run harness panel.
 *
 * Runs detection, lists what it found with detected harnesses pre-ticked, offers a
 * separate "scan history" choice, and a skip that writes nothing but the
 * completion flag. First-run state is daemon-side (`harness_setup_complete`), not
 * device-local, because harness enablement is machine config: a choice made on the
 * desktop must not reappear on the phone.
 *
 * `onConfigureMore` hands off to Settings -> Agents for per-harness executable,
 * arguments, and width editing, so this panel assembles the enable/scan parts
 * rather than duplicating that surface.
 */
export function HarnessSetup(
  { onDone, onConfigureMore }: { onDone: () => void; onConfigureMore: () => void },
) {
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

  // Skip writes nothing but the completion flag: no `harness_enabled` entries, so a
  // harness installed later is still picked up by detection.
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

  return <div class="harness-setup-backdrop" role="dialog" aria-modal="true" aria-label="Set up agents">
    <section class="harness-setup">
      <header><strong>SET UP::AGENTS</strong></header>
      <div class="harness-setup-body">
        <p>swe-mux found these agent CLIs on this machine. Enabled harnesses appear in the launchers; you can change any of this later under Settings → Agents.</p>
        <p class="harness-setup-note">When mux launches an enabled agent it adds two things per session: its lifecycle hooks (so status, history, and the prompt queue work) and a read-only mux MCP server (so the agent can see the fleet). Both are per-session and removed when the session ends. You can turn either off per harness under Settings → Agents ("launch clean" runs an agent unobserved).</p>
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
