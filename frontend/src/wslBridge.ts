import { t } from './localization/index.ts'
// Types and decision logic for the WSL agent bridge setup surface.
//
// Split from the component (`wslBridge.tsx`) rather than living beside it because
// this half is the part worth testing: which blocker a user is shown, and in which
// order, is the whole value of the panel. The node test runner also cannot load a
// `.tsx`, which is why the codebase keeps logic in `.ts` throughout.

export type WslBridgeHarness = { name: string; executable: string }

export type WslBridgeDetail = {
  distro: string
  available: boolean
  installed: boolean
  reachable?: boolean | null
  harnesses: WslBridgeHarness[]
  linux_home?: string
  windows_home?: string
  host_address?: string
  reasons: string[]
}

export type WslDistro = { name: string; running: boolean; bridge?: WslBridgeDetail }

export type WslBridgeStatus = {
  supported: boolean
  enabled: boolean
  reason?: string
  adapter_address?: string | null
  adapter_subnet?: string | null
  daemon_port?: number
  restart_required?: boolean
  firewall_rule?: string
  distros: WslDistro[]
}

/**
 * What is stopping this distribution from hosting an *observed* agent, in the
 * order a user would fix them.
 *
 * Ordered rather than merely listed, because the order is the advice. Telling
 * someone to install a bridge into a distribution with no agent in it is noise;
 * sending them to the firewall after an install that could never have phoned home
 * is worse than noise. And when the feature is simply off, that is the only thing
 * worth saying - every other blocker is downstream of the switch.
 */
export function blockers(status: WslBridgeStatus, distro: WslDistro): string[] {
  if (!status.enabled) {
    return [
      t("wslBridge.214da0041d"),
    ]
  }
  const bridge = distro.bridge
  if (!bridge) return [t("wslBridge.fb1e0435d6")]
  const found: string[] = []
  if (!bridge.harnesses.length) {
    found.push(
      t("wslBridge.471c663416"),
    )
  }
  if (bridge.reachable === false) {
    found.push(
      t("wslBridge.0acbd678a5"),
    )
  }
  if (bridge.harnesses.length && !bridge.installed) {
    found.push(t("wslBridge.0c27b6ecb1"))
  }
  // Anything the daemon reported that the cases above do not already cover - a
  // missing python3, a distribution that would not start. Falling back to the
  // daemon's own words beats inventing a generic "unavailable".
  if (!found.length && !bridge.available) found.push(...bridge.reasons)
  return found
}

/** Whether this distribution can host an agent mux will actually observe. */
export function isReady(status: WslBridgeStatus, distro: WslDistro): boolean {
  return Boolean(status.enabled && distro.bridge?.available && distro.bridge?.installed)
}
