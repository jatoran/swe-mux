import { locale as uiLocale } from './localization/index.ts'
import { t } from './localization/index.ts'
/**
 * The frontend overlay, browser side: the payload shape and the one sentence to say.
 *
 * The overlay is a hash-verified `static/` tree in the daemon's data dir that it
 * serves instead of its own bundled one, so a UI fix reaches a frozen desktop app
 * without a bundle swap. All of the deciding - verification, the compatibility
 * pin against the backend version, which tree won - happens in the daemon and
 * arrives here as `serving`. Nothing is re-derived here, for the reason
 * `updateCheck.ts` does not re-derive `banner`: a second implementation of the
 * hard part is how desktop and phone end up disagreeing.
 *
 * The line worth drawing is the *faulted* one. An overlay that is installed and
 * refused looks, from a browser, exactly like one that was never installed - and
 * that is precisely the "a verified-correct fix silently does nothing" failure
 * this feature exists to end. So the daemon reports `faulted` separately from
 * "not serving an overlay", and the UI says so out loud.
 */

// Extension-qualified so this module resolves under `node --experimental-strip-types`,
// which is what runs the unit suite: an extensionless specifier is fine for the bundler
// and unresolvable there, and a module the runner cannot load takes its test file with it.
import { api } from './api.ts'

/** The manifest summary, without the file map (a hundred lines nobody reads). */
export type OverlaySummary = {
  schema: number
  requires_backend: string
  /**
   * A digest over the route table the overlay was built against. The half of the
   * pin `requires_backend` cannot supply: a frozen app is rebuilt from a checkout
   * that moves per commit while the version string moves per release, so two
   * sides can agree on the version and disagree about which endpoints exist.
   */
  requires_api: string
  tree_digest: string
  ui_build_id: string | null
  built_at: number | null
  source: string
  file_count: number
}

export type OverlayServing = {
  /** `overlay` or `bundled`. */
  serving: string
  directory: string
  bundled_directory: string
  /**
   * The daemon's own word: `ok`, `no_overlay`, `reverted`, `disabled`, or one of
   * the fault reasons (`version_mismatch`, `hash_mismatch`, `missing_file`,
   * `unexpected_file`, ...). Kept rather than collapsed into a boolean, because
   * "nothing is installed" and "what you installed was refused" read the same
   * otherwise and mean opposite things.
   */
  reason: string
  message: string
  /** Installed, switched on, and not serveable. The one case worth shouting about. */
  faulted: boolean
  overlay: OverlaySummary | null
}

export type OverlayStatus = {
  supported: boolean
  installed: boolean
  active: boolean
  backend_version?: string
  can_restore?: boolean
  tree_exists?: boolean
  state?: {
    active: boolean
    digest: string
    previous_digest: string
    requires_backend: string
    requires_api: string
    ui_build_id: string
    installed_at: number | null
    installed_from: string
    reverted_at: number | null
  }
  /** Null when a caller passed an explicit frontend directory override. */
  serving: OverlayServing | null
  override?: boolean
}

/** The gesture headers the three write endpoints require, one word each. */
export const OVERLAY_INSTALL_GESTURE = 'frontend-overlay-install'
export const OVERLAY_REVERT_GESTURE = 'frontend-overlay-revert'
export const OVERLAY_RESTORE_GESTURE = 'frontend-overlay-restore'

/**
 * Whether Settings should draw the overlay section at all.
 *
 * Hidden on a daemon that has none, and hidden when nothing is installed on a
 * daemon that supports it: an empty panel explaining a mechanism nobody is using
 * is noise on a page that already has a lot of it. It appears the moment an
 * overlay exists, which is the moment there is something to revert.
 */
export function shouldShowOverlaySection(status: OverlayStatus | null): boolean {
  return !!status && status.supported === true && status.installed === true
}

/**
 * Whether to warn. True only for the case a browser cannot otherwise see:
 * something is installed, switched on, and not being served.
 */
export function overlayIsFaulted(status: OverlayStatus | null): boolean {
  return !!status && status.installed === true && status.serving?.faulted === true
}

/**
 * The one line the section says about the current state.
 *
 * Every branch is a real case rather than defensive padding, and the order is
 * the order a reader needs: a fault first because it is the only one that means
 * something is wrong, then the two deliberate off states, then the good one.
 */
export function overlayStatusSummary(status: OverlayStatus | null): string | null {
  if (!status || !status.supported) return null
  if (!status.installed) return null
  const serving = status.serving
  if (serving?.faulted) {
    return serving.message || t("frontendOverlay.4aed3952c9", { value1: serving.reason })
  }
  if (serving?.reason === 'disabled') {
    return t("frontendOverlay.b73232a16f")
  }
  if (!status.active) {
    return t("frontendOverlay.045c16f6f3")
  }
  if (serving?.serving === 'overlay') {
    return t("frontendOverlay.110682792e")
  }
  // Installed, switched on, and this process is still on the bundle: the daemon
  // resolved before the install, so a restart is what is missing. Saying that is
  // the difference between a pending change and a broken one.
  return t("frontendOverlay.6b05e7c57f")
}

/** When it was installed. Absolute, for the reason the update check's is. */
export function installedAtLabel(status: OverlayStatus | null): string | null {
  const seconds = status?.state?.installed_at
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return null
  return new Date(seconds * 1000).toLocaleString(uiLocale.value)
}

/** The passive read. Reads daemon state; reaches nothing past it. */
export const fetchOverlayStatus = (): Promise<OverlayStatus> =>
  api<OverlayStatus>('GET', '/api/frontend/overlay', undefined, { timeoutMs: 10_000 })

async function press(path: string, gesture: string): Promise<OverlayStatus> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Mux-User-Gesture': gesture },
    body: '{}',
  })
  const payload = await response.json() as { error?: string; message?: string }
  if (!response.ok) throw new Error(payload.message || payload.error || t("frontendOverlay.8816661d68"))
  return fetchOverlayStatus()
}

/**
 * Switch the overlay off. One boolean in one small file on the daemon's side, so
 * this cannot half-succeed - and the status is re-read afterwards rather than
 * patched locally, because the answer that matters is the daemon's.
 */
export const revertOverlay = (): Promise<OverlayStatus> =>
  press('/api/frontend/overlay/revert', OVERLAY_REVERT_GESTURE)

/** Switch a reverted overlay back on. The exact inverse. */
export const restoreOverlay = (): Promise<OverlayStatus> =>
  press('/api/frontend/overlay/restore', OVERLAY_RESTORE_GESTURE)
