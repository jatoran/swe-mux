import assert from 'node:assert/strict'
import test from 'node:test'
import {
  installedAtLabel, overlayIsFaulted, overlayStatusSummary, shouldShowOverlaySection,
  OVERLAY_INSTALL_GESTURE, OVERLAY_RESTORE_GESTURE, OVERLAY_REVERT_GESTURE,
  type OverlayStatus,
} from '../src/frontendOverlay.ts'

/**
 * The browser half of the frontend overlay.
 *
 * The verification and the compatibility pin are the daemon's and are tested in
 * `tests/test_frontend_overlay.py`. What is pinned here is the one thing the
 * browser decides, and it is the whole point of the feature having a UI at all:
 * **an overlay that is installed and refused must not read as an overlay that
 * was never installed.** From inside a browser those two are indistinguishable -
 * both show the bundled frontend - and confusing them is exactly the "a
 * verified-correct fix silently does nothing" failure the overlay exists to end.
 */

const status = (extra: Partial<OverlayStatus> = {}): OverlayStatus => ({
  supported: true,
  installed: true,
  active: true,
  backend_version: '0.1.2',
  can_restore: false,
  tree_exists: true,
  state: {
    active: true,
    digest: 'd'.repeat(64),
    previous_digest: '',
    requires_backend: '0.1.2',
    requires_api: 'b'.repeat(64),
    ui_build_id: 'a'.repeat(64),
    installed_at: 1_756_000_000,
    installed_from: 'D:/PROJECTS/swe-mux/dist/swe-mux-0.1.2-ui.zip',
    reverted_at: null,
  },
  serving: {
    serving: 'overlay',
    directory: 'C:/Users/x/.mux/frontend-overlay/trees/dddd',
    bundled_directory: 'C:/app/_internal/swe_mux/static',
    reason: 'ok',
    message: '',
    faulted: false,
    overlay: {
      schema: 1,
      requires_backend: '0.1.2',
      requires_api: 'b'.repeat(64),
      tree_digest: 'd'.repeat(64),
      ui_build_id: 'a'.repeat(64),
      built_at: 1_756_000_000,
      source: 'packaging/build_frontend_overlay.py',
      file_count: 60,
    },
  },
  ...extra,
})

test('the section appears only once there is an overlay to talk about', () => {
  assert.equal(shouldShowOverlaySection(status()), true)
  // A fetch that failed or has not returned yet.
  assert.equal(shouldShowOverlaySection(null), false)
  // An older daemon that has no such endpoint answers `supported: false`.
  assert.equal(shouldShowOverlaySection(status({ supported: false })), false)
  // Supported, but nothing installed: an empty panel explaining a mechanism
  // nobody is using is noise on a page that already has a lot of it.
  assert.equal(shouldShowOverlaySection(status({ installed: false })), false)
})

test('a refused overlay is a fault and an absent one is not', () => {
  const refused = status({
    serving: { ...status().serving!, serving: 'bundled', reason: 'version_mismatch', faulted: true },
  })
  assert.equal(overlayIsFaulted(refused), true)
  assert.equal(overlayIsFaulted(status()), false)
  // Nothing installed cannot be a fault, however the daemon phrases it.
  assert.equal(overlayIsFaulted(status({ installed: false })), false)
  assert.equal(overlayIsFaulted(null), false)
})

test('a deliberate revert is not reported as a fault', () => {
  // The distinction that keeps the warning meaningful: somebody chose this.
  const reverted = status({
    active: false,
    can_restore: true,
    serving: { ...status().serving!, serving: 'bundled', reason: 'reverted', faulted: false },
  })
  assert.equal(overlayIsFaulted(reverted), false)
  assert.match(overlayStatusSummary(reverted) ?? '', /reverted/i)
})

test('the summary says why, not merely that', () => {
  // The daemon writes a sentence naming both versions; repeating a generic
  // "not being used" would throw away the only line that explains the state.
  const refused = status({
    serving: {
      ...status().serving!,
      serving: 'bundled',
      reason: 'version_mismatch',
      message: 'The overlay is built for swe-mux 0.1.1 and this daemon is 0.1.2.',
      faulted: true,
    },
  })
  assert.match(overlayStatusSummary(refused) ?? '', /0\.1\.1/)
  assert.match(overlayStatusSummary(refused) ?? '', /0\.1\.2/)
})

test('the confusing refusal is the one whose sentence must survive', () => {
  // `api_mismatch` is the case where both sides report the same version and
  // disagree anyway. A generic "the overlay is not being used" here would strand
  // a reader who can see 0.1.2 on both ends and no reason for anything to differ.
  const refused = status({
    serving: {
      ...status().serving!,
      serving: 'bundled',
      reason: 'api_mismatch',
      message: 'The overlay was built against a different set of daemon endpoints. '
        + 'Rebuild the overlay from the same checkout this daemon was built from, or redeploy.',
      faulted: true,
    },
  })
  assert.equal(overlayIsFaulted(refused), true)
  assert.match(overlayStatusSummary(refused) ?? '', /endpoints/)
  assert.match(overlayStatusSummary(refused) ?? '', /redeploy/)
})

test('a refusal with no sentence still names its reason', () => {
  const refused = status({
    serving: {
      ...status().serving!, serving: 'bundled', reason: 'hash_mismatch', message: '', faulted: true,
    },
  })
  assert.match(overlayStatusSummary(refused) ?? '', /hash_mismatch/)
})

test('an install waiting on a restart is distinguished from a broken one', () => {
  // The daemon resolved before the install, so it is still on the bundle. That is
  // a pending change, not a failure, and saying "refused" here would send someone
  // hunting for a fault that does not exist.
  const pending = status({
    serving: { ...status().serving!, serving: 'bundled', reason: 'no_overlay', faulted: false },
  })
  const summary = overlayStatusSummary(pending) ?? ''
  assert.match(summary, /restart/i)
  assert.doesNotMatch(summary, /refused/i)
})

test('the install-wide switch reads as a choice rather than a failure', () => {
  const off = status({
    serving: { ...status().serving!, serving: 'bundled', reason: 'disabled', faulted: false },
  })
  assert.match(overlayStatusSummary(off) ?? '', /turned off/i)
  assert.equal(overlayIsFaulted(off), false)
})

test('nothing is said about a daemon with no overlay support or no overlay', () => {
  assert.equal(overlayStatusSummary(null), null)
  assert.equal(overlayStatusSummary(status({ supported: false })), null)
  assert.equal(overlayStatusSummary(status({ installed: false })), null)
})

test('the installed timestamp survives a payload that carries none', () => {
  assert.equal(typeof installedAtLabel(status()), 'string')
  assert.equal(installedAtLabel(status({ state: undefined })), null)
  assert.equal(installedAtLabel(null), null)
  const broken = status()
  broken.state!.installed_at = null
  assert.equal(installedAtLabel(broken), null)
})

test('the three gestures are three distinct words', () => {
  // One shared token would let a client holding the header for a revert spend it
  // on an install, which is the one of the three that replaces the UI.
  const gestures = [OVERLAY_INSTALL_GESTURE, OVERLAY_REVERT_GESTURE, OVERLAY_RESTORE_GESTURE]
  assert.equal(new Set(gestures).size, 3)
  assert.equal(OVERLAY_INSTALL_GESTURE, 'frontend-overlay-install')
  assert.equal(OVERLAY_REVERT_GESTURE, 'frontend-overlay-revert')
  assert.equal(OVERLAY_RESTORE_GESTURE, 'frontend-overlay-restore')
})
