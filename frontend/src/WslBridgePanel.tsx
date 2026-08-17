// Setup surface for the WSL agent bridge.
//
// This exists because the bridge's failure mode is silence. An agent running
// inside a distribution that cannot reach the daemon starts perfectly, answers
// perfectly, and simply never reports - no hooks, no transcript link, no status.
// Nothing in the terminal looks wrong, so the only place the truth can appear is a
// panel that goes looking and says what is missing.
//
// Deliberately shaped like the Windows firewall panel next to it: read status,
// state the blocker in a sentence, offer exactly the action that clears it. The
// two even share the elevation path, so a user who has repaired one is not
// surprised by the other.
//
// The decision logic lives in `wslBridge.ts` and is tested there.
import { blockers, isReady, type WslBridgeStatus } from './wslBridge'

export type {
  WslBridgeDetail,
  WslBridgeHarness,
  WslBridgeStatus,
  WslDistro,
} from './wslBridge'

export function WslBridgePanel({
  status,
  busy,
  message,
  probing,
  onToggle,
  onProbe,
  onInstall,
  onRepairFirewall,
}: {
  status: WslBridgeStatus | null
  busy: string
  message: string
  probing: boolean
  onToggle: (enabled: boolean) => void
  onProbe: () => void
  onInstall: (distro: string) => void
  onRepairFirewall: () => void
}) {
  // A host without WSL is not misconfigured, so there is nothing to say.
  if (!status || !status.supported) return null
  const needsFirewall = status.distros.some(d => d.bridge?.reachable === false)
  return <div class="remote-firewall">
    <strong>WSL agent bridge</strong>
    <p class="profile-hint">Run an agent CLI natively inside a WSL distribution and have mux observe it - hooks, status, transcripts - the same as a Windows pane. Without the bridge a WSL pane can still start an agent, but mux sees none of it.</p>
    <label><input type="checkbox" checked={status.enabled} onChange={e => onToggle(e.currentTarget.checked)} /> Enable the WSL agent bridge</label>
    {status.enabled && <p class="profile-hint">The daemon listens on {status.adapter_address || 'the WSL adapter'} for hooks from inside a distribution. Every process in every distribution on this machine can then reach it, and swe-mux has no login of its own.</p>}
    {status.restart_required && <p class="settings-inline-error" aria-live="polite">Restart the daemon so it binds {status.adapter_address}. Enabling this changes which sockets the daemon opens, and that only happens at startup.</p>}

    <div class="theme-actions">
      <button disabled={probing} onClick={onProbe}>{probing ? 'Checking…' : 'Check distributions'}</button>
      {needsFirewall && <button class="primary" disabled={busy === 'firewall'} onClick={onRepairFirewall}>{busy === 'firewall' ? 'Repairing…' : 'Allow through firewall'}</button>}
    </div>
    <p class="profile-hint">Checking starts a stopped distribution, which takes a few seconds - so it is a button rather than something this page does on its own.</p>

    {!status.distros.length && <p class="profile-hint">No WSL distributions found.</p>}
    {status.distros.map(distro => <div key={distro.name} class="wsl-distro">
      <strong>{distro.name}</strong> <span class="profile-hint">{distro.running ? 'running' : 'stopped'}</span>
      {isReady(status, distro)
        ? <p class="profile-hint">Ready: {distro.bridge?.harnesses.map(h => h.name).join(', ')}</p>
        : <ul class="profile-hint">{blockers(status, distro).map(reason => <li key={reason}>{reason}</li>)}</ul>}
      {status.enabled && distro.bridge?.harnesses.length && !distro.bridge?.installed
        ? <div class="theme-actions"><button disabled={busy === distro.name} onClick={() => onInstall(distro.name)}>{busy === distro.name ? 'Installing…' : `Install bridge into ${distro.name}`}</button></div>
        : null}
    </div>)}

    {message && <p class={/could not|failed|declined/i.test(message) ? 'settings-inline-error' : ''} aria-live="polite">{message}</p>}
  </div>
}
