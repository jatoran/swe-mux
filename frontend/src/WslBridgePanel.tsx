import { rich, t } from './localization/index.ts'
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
    <strong>{t("WslBridgePanel.6db147570d")}</strong>
    <p class="profile-hint">{t("WslBridgePanel.1f2117f696")}</p>
    <label>{rich("WslBridgePanel.00336e77f8", { value1: <input type="checkbox" checked={status.enabled} onChange={e => onToggle(e.currentTarget.checked)} /> })}</label>
    {status.enabled && <p class="profile-hint">{rich("WslBridgePanel.71c75faecc", { value1: status.adapter_address || t("WslBridgePanel.9f853b9c17") })}</p>}
    {status.restart_required && <p class="settings-inline-error" aria-live="polite">{rich("WslBridgePanel.56e04f894b", { adapter_address: status.adapter_address })}</p>}

    <div class="theme-actions">
      <button disabled={probing} onClick={onProbe}>{probing ? t("WslBridgePanel.ec963ffc91") : t("WslBridgePanel.94cb7ed354")}</button>
      {needsFirewall && <button class="primary" disabled={busy === 'firewall'} onClick={onRepairFirewall}>{busy === 'firewall' ? t("WslBridgePanel.fb4c616eb4") : t("WslBridgePanel.4a988a055e")}</button>}
    </div>
    <p class="profile-hint">{t("WslBridgePanel.d9d8073e67")}</p>

    {!status.distros.length && <p class="profile-hint">{t("WslBridgePanel.25872d015b")}</p>}
    {status.distros.map(distro => <div key={distro.name} class="wsl-distro">
      <strong>{distro.name}</strong> <span class="profile-hint">{distro.running ? t("WslBridgePanel.c071cf5f5e") : t("WslBridgePanel.8322e87d24")}</span>
      {isReady(status, distro) ? <p class="profile-hint">{rich("WslBridgePanel.e5e5b6d309", { value1: distro.bridge?.harnesses.map(h => h.name).join(', ') })}</p> : <ul class="profile-hint">{blockers(status, distro).map(reason => <li key={reason}>{reason}</li>)}</ul>}
      {status.enabled && distro.bridge?.harnesses.length && !distro.bridge?.installed ? <div class="theme-actions"><button disabled={busy === distro.name} onClick={() => onInstall(distro.name)}>{busy === distro.name ? t("WslBridgePanel.530bcc355f") : t("WslBridgePanel.6407a06987", { name: distro.name })}</button></div> : null}
    </div>)}

    {message && <p class={/could not|failed|declined/i.test(message) ? 'settings-inline-error' : ''} aria-live="polite">{message}</p>}
  </div>
}
