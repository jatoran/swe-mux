/**
 * Which install-wide switches are on, for the surfaces that go inert without one.
 *
 * The exact counterpart of `projectAutomations.ts`, and it exists for the same reason:
 * a surface downstream of an off switch cannot tell "off" from "quiet" unless it can
 * read the switch, and a surface that cannot tell them apart renders an empty panel that
 * will never fill in however long you wait.
 *
 * Settings owns the *editing* of these and holds a staged draft of them; this is the
 * plain current state, shared and cached because several drawer panes ask the same
 * question about the same daemon within a second of each other. The cache is dropped on
 * the daemon's own `configuration_changed` (re-broadcast by `App` as
 * `mux:install-config-changed`) and on a local grant, never on a timer - a timer would
 * be wrong in both directions.
 */

import { api } from './api'
import { GRANTS_CHANGED } from './grants'

export const INSTALL_CONFIG_CHANGED = 'mux:install-config-changed'

/** Only the booleans a gate reads. The full config is Settings' business. */
export type InstallSwitches = Record<string, unknown>

let cached: Promise<InstallSwitches> | null = null

export function forgetInstallSwitches(): void { cached = null }

export function fetchInstallSwitches(): Promise<InstallSwitches> {
  if (cached) return cached
  const pending = api<InstallSwitches>('GET', '/api/config')
    // A failed read is not evidence that anything is off, so it is not remembered as
    // one: the next caller retries rather than inheriting an error as a state.
    .catch(error => { cached = null; throw error })
  cached = pending
  return pending
}

/**
 * Read one switch, three-valued.
 *
 * `null` means "not read yet or unreadable", which every caller must render differently
 * from `false`: claiming a feature is switched off because a fetch failed is the same
 * lie as claiming a Project is quiet because its opt-in could not be read.
 */
export async function installSwitch(key: string): Promise<boolean | null> {
  try {
    const value = (await fetchInstallSwitches())[key]
    return typeof value === 'boolean' ? value : null
  } catch { return null }
}

if (typeof window !== 'undefined') {
  window.addEventListener(INSTALL_CONFIG_CHANGED, () => forgetInstallSwitches())
  window.addEventListener(GRANTS_CHANGED, () => forgetInstallSwitches())
}
