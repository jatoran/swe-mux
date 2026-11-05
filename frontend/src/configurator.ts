import { t } from './localization/index.ts'
/**
 * The configurator launcher: one button that opens an agent session about swe-mux
 * itself.
 *
 * There is almost nothing to this in the browser, and that is deliberate. The
 * daemon resolves which harness to use, which Project anchors the session, and
 * what the opening prompt says, because every one of those answers depends on
 * facts the browser does not hold (live CLI detection, whether this install has a
 * source checkout, the current health report). What is left here is the part that
 * genuinely is presentation: deciding what the control should *say* before it is
 * pressed, so a press that cannot succeed is a disabled button with a reason
 * rather than an error dialog.
 *
 * That split is why `launchState` is a pure function over the options payload and
 * is unit-tested. The failure it exists to prevent is a launcher that looks ready
 * on a machine with no agent CLI installed - the operator presses it, waits, and
 * gets a refusal that reads as a bug rather than as the missing prerequisite it is.
 */

import { api } from './api.ts'

/** What the daemon can offer, asked once when a surface holding the button opens. */
export type ConfiguratorOptions = {
  /** Agent harnesses that are both registered and available on this machine. */
  harnesses: string[]
  /** The one a plain press would use, or null when there is none to use. */
  default_harness: string | null
  /** The operator's explicit `default_harness` setting; '' means resolve by detection. */
  configured_default: string
  install_mode: 'source' | 'frozen' | 'installed'
  source_checkout: string
  /** How many Projects are registered. A session needs one. */
  projects: number
}

/**
 * Why the launcher cannot run right now, or null when it can.
 *
 * Two blockers, and they are different problems with different fixes, so they
 * never share a message: no agent CLI is a *prerequisite* the operator has to
 * install, and no Project is a *setup step* they can complete in the app.
 */
export type ConfiguratorBlocker = 'no-harness' | 'no-project'

export type ConfiguratorLaunchState = {
  enabled: boolean
  blocker: ConfiguratorBlocker | null
  /** Tooltip/label text. Always says what pressing would do, or why it cannot. */
  reason: string
  /** The harness a plain press uses, '' when blocked. */
  harness: string
}

const noHarnessMessage =
  () => (t("configurator.771cd33082") +
  t("configurator.6c423d198e"))
const noProjectMessage =
  () => (t("configurator.31290470da") +
  t("configurator.6fe0df5ed6"))

/**
 * What the button should say and whether it should be pressable.
 *
 * A null payload (the options request has not answered, or failed) leaves the
 * control enabled with a neutral label rather than disabled: a launcher that
 * greys itself out because a status request is in flight reads as broken, and the
 * daemon refuses cleanly on the press anyway.
 */
export function launchState(options: ConfiguratorOptions | null): ConfiguratorLaunchState {
  if (!options) {
    return { enabled: true, blocker: null, get reason() { return t("configurator.676aebfe2c") }, harness: '' }
  }
  if (!options.harnesses.length || !options.default_harness) {
    return { enabled: false, blocker: 'no-harness', reason: noHarnessMessage(), harness: '' }
  }
  if (options.projects < 1) {
    return { enabled: false, blocker: 'no-project', reason: noProjectMessage(), harness: '' }
  }
  return {
    enabled: true,
    blocker: null,
    get reason() { return t("configurator.0cb3545539", { default_harness: options.default_harness }) },
    harness: options.default_harness,
  }
}

/**
 * Whether pressing should open the harness chooser instead of launching.
 *
 * A plain press launches the default, because that is the whole value of the
 * control: one press, no decision. The chooser is for the machine with several
 * agents installed and an operator who wants a different one this time, so it is
 * on the modifiers that already mean "give me the options" — the same gesture
 * that opens a context menu.
 */
export function opensChooser(
  event: Pick<MouseEvent, 'button' | 'shiftKey' | 'altKey'>,
  options: ConfiguratorOptions | null,
): boolean {
  if (!(event.button === 2 || event.shiftKey || event.altKey)) return false
  // With one candidate there is nothing to choose, so even an explicit ask falls
  // through to launching it rather than opening a menu with a single row.
  return (options?.harnesses.length ?? 0) > 1
}

export async function fetchConfiguratorOptions(): Promise<ConfiguratorOptions> {
  return api<ConfiguratorOptions>('GET', '/api/configurator/options', undefined, {
    timeoutMs: 10_000,
  })
}

/** The daemon route a launch posts to. */
export const CONFIGURATOR_LAUNCH_PATH = '/api/configurator/launch'

/**
 * The launch body. `harness` empty means "whatever the daemon resolves", which is
 * not the same as guessing here and sending it: the daemon re-resolves against
 * live detection, so a CLI uninstalled since the options request cannot produce a
 * launch that half-succeeds.
 */
export function launchBody(projectId: string, harness = ''): Record<string, string> {
  return { project_id: projectId, ...(harness ? { harness } : {}) }
}
