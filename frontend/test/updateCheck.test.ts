import assert from 'node:assert/strict'
import test from 'node:test'
import {
  bytesLabel, CONSENT_SUPERVISOR_UPDATE, installFinished, installPhaseLabel, lastCheckedLabel,
  planCostLine, planInstallLine, planSessionsLine, refusalNeedsConsent, shouldShowUpdateBanner,
  updateBannerText, updateStatusSummary, UPDATE_CHECK_GESTURE, UPDATE_INSTALL_GESTURE,
  UPDATE_PLAN_GESTURE,
  type UpdatePlan, type UpdateStatus,
} from '../src/updateCheck.ts'

/**
 * The browser half of the release update check.
 *
 * The comparison itself is the daemon's and is tested in `tests/test_update_check.py`;
 * what is pinned here is the narrow thing the browser decides - whether there is
 * anything to draw. Every clause of that guard is a state a real daemon returns:
 * an install that has never checked, one that could not reach the site, one whose
 * operator declined the version, and an older daemon that has no such endpoint at
 * all and answers without a `banner` field.
 */

const status = (extra: Partial<UpdateStatus> = {}): UpdateStatus => ({
  enabled: true,
  status: 'ok',
  current_version: '0.1.0',
  update_available: true,
  latest: {
    version: '0.2.0',
    tag: 'v0.2.0',
    published: '2026-08-27T00:00:00Z',
    changelog: 'https://example.invalid/releases/tag/v0.2.0',
    source: 'manifest',
  },
  banner: true,
  ...extra,
})

test('the banner follows the daemon verdict rather than re-deriving it', () => {
  // Two comparisons would eventually disagree, and the phone and the desktop
  // would then differ about whether an update exists. `banner` is the one answer.
  assert.equal(shouldShowUpdateBanner(status()), true)
  assert.equal(shouldShowUpdateBanner(status({ banner: false })), false)
  // `update_available` alone is not the trigger: a declined version keeps this
  // true and must still draw nothing.
  assert.equal(
    shouldShowUpdateBanner(status({ banner: false, update_available: true })),
    false,
  )
})

test('nothing is drawn without a payload to draw it from', () => {
  // A fetch that failed or has not returned yet.
  assert.equal(shouldShowUpdateBanner(null), false)
  // An older daemon with no update endpoint: the field is simply absent, and a
  // truthiness check on it would be the same bug as trusting it.
  assert.equal(shouldShowUpdateBanner({ update_available: true } as unknown as UpdateStatus), false)
  // A verdict with nothing to name or link to is not a banner.
  assert.equal(shouldShowUpdateBanner(status({ latest: null })), false)
  assert.equal(
    shouldShowUpdateBanner(status({ latest: { ...status().latest!, version: '' } })),
    false,
  )
})

test('the banner names both versions and claims nothing else', () => {
  // Deliberately no severity word: the manifest carries none, so "critical" or
  // "recommended" here would be the banner inventing a fact.
  const text = updateBannerText(status())
  assert.match(text, /0\.2\.0/)
  assert.match(text, /0\.1\.0/)
  assert.doesNotMatch(text, /critical|security|urgent|recommended/i)
  // A daemon that did not report its own version still gets a usable sentence.
  assert.equal(
    updateBannerText(status({ current_version: undefined })),
    'swe-mux 0.2.0 is available.',
  )
})

test('every check outcome has its own sentence, and none of them read as an error', () => {
  // The point of keeping the daemon's word rather than a boolean: "we have not
  // looked yet" and "we looked and could not tell" are different facts, and a
  // single "update status unknown" would collapse them.
  const summary = (state: string, extra: Partial<UpdateStatus> = {}) =>
    updateStatusSummary(status({ status: state, ...extra }))
  assert.equal(summary('never_checked'), 'Not checked yet.')
  assert.equal(summary('ok', { update_available: false }), 'This is the latest release.')
  assert.match(String(summary('ok')), /0\.2\.0/)
  assert.match(String(summary('unreachable')), /could not reach/)
  assert.match(String(summary('malformed')), /did not answer/)
  assert.match(String(summary('unsupported_schema')), /newer format/)
  assert.match(String(summary('incomparable')), /could not compare/)
  assert.match(String(summary('disabled')), /Nothing is requested/)
  // Two silences, both deliberate: no payload, and a daemon that has no checker.
  assert.equal(updateStatusSummary(null), null)
  assert.equal(summary('unavailable'), null)
  assert.equal(summary('something-a-later-build-invented'), null)
})

test('a last-checked line appears only when there is a real timestamp', () => {
  assert.equal(lastCheckedLabel(null), null)
  assert.equal(lastCheckedLabel(status({ checked_at: null })), null)
  assert.equal(lastCheckedLabel(status({ checked_at: undefined })), null)
  // Not a number is not a time; rendering `Invalid Date` under a control is worse
  // than rendering no line.
  assert.equal(lastCheckedLabel(status({ checked_at: Number.NaN })), null)
  assert.equal(
    lastCheckedLabel(status({ checked_at: 1_756_000_000 })),
    new Date(1_756_000_000_000).toLocaleString(),
  )
})

test('the gesture header the daemon requires is spelled the same on both sides', () => {
  // The daemon refuses `POST /api/update/check` without exactly this value, and a
  // drift here would turn the button into a silent 400. The plan and the install
  // carry their own words, deliberately different from each other.
  assert.equal(UPDATE_CHECK_GESTURE, 'update-check')
  assert.equal(UPDATE_PLAN_GESTURE, 'update-plan')
  assert.equal(UPDATE_INSTALL_GESTURE, 'update-install')
  assert.equal(CONSENT_SUPERVISOR_UPDATE, 'supervisor_update')
})

// --- installing: rendering the daemon's plan and its refusals ---------------------

const plan = (extra: Partial<UpdatePlan> = {}): UpdatePlan => ({
  version: '0.2.0',
  current_version: '0.1.0',
  changelog: 'https://example.invalid/releases/tag/v0.2.0',
  published: '2026-09-05T00:00:00Z',
  install_kind: 'frozen',
  managed: 'installer',
  install_root: 'C:\\Users\\me\\AppData\\Local\\Programs\\swe-mux',
  artifact: { name: 'swe-mux-0.2.0-windows-x64.zip', url: 'https://x/', sha256: 'ab' },
  installer: null,
  supervisor: {
    mode: 'swap', reason: '', message: 'Your live sessions are preserved.',
    running_protocol: 1, incoming_protocol: 1, known: true, reaps_sessions: false, consent: '',
  },
  mode: 'swap',
  reaps_sessions: false,
  consent: '',
  consent_reason: '',
  delta: { eligible: true, fetch_files: 63, fetch_bytes: 32_400_000, reuse_files: 2874 },
  archive_cached: false,
  live_sessions: 7,
  ...extra,
})

test('the sessions line is the daemon verdict with the count the operator would lose', () => {
  // Three facts, three sentences, and the count is only ever the daemon's. A
  // reap names the number because that is what the press costs.
  assert.match(planSessionsLine(plan()), /7 live sessions keep running/)
  assert.match(planSessionsLine(plan({ live_sessions: 1 })), /Your 1 live session keep/)
  const reap = plan({
    reaps_sessions: true, mode: 'replace', consent: 'supervisor_update',
    supervisor: { ...plan().supervisor, mode: 'replace', reaps_sessions: true, reason: 'supervisor_update_required' },
  })
  assert.match(planSessionsLine(reap), /ends every live terminal session - 7 live sessions right now/)
  assert.match(planSessionsLine({ ...reap, live_sessions: 0 }), /None are running right now/)
  // A release published without the metadata sidecar: the plan does not guess.
  const unknown = plan({ supervisor: { ...plan().supervisor, known: false, incoming_protocol: null } })
  assert.match(planSessionsLine(unknown), /decided after the download/)
  assert.match(planSessionsLine(unknown), /asks first/)
})

test('the cost line counts files rewritten, or says the archive is already here', () => {
  assert.equal(
    planCostLine(plan()),
    'Downloads the release and rewrites 63 files (32.4 MB); 2874 already on this machine are reused.',
  )
  assert.match(planCostLine(plan({ delta: { eligible: true, fetch_files: 1, fetch_bytes: 500 }, })), /rewrites 1 file \(500 B\)/)
  assert.equal(planCostLine(plan({ delta: { eligible: false }, archive_cached: true })), 'The release is already downloaded and verified.')
  assert.equal(planCostLine(plan({ delta: { eligible: false } })), '')
})

test('the install line names how this copy is managed, and nothing for a shape it does not know', () => {
  assert.match(planInstallLine(plan({ managed: 'installer' })), /Add\/Remove Programs/)
  assert.match(planInstallLine(plan({ managed: 'portable' })), /portable/)
  assert.match(planInstallLine(plan({ managed: 'checkout' })), /dist\//)
  assert.equal(planInstallLine(plan({ managed: '' })), '')
})

test('a refusal needs consent only when the daemon says that word', () => {
  assert.equal(refusalNeedsConsent({ error: 'supervisor_update_required', message: '', consent: 'supervisor_update', install_kind: 'frozen', swappable: true, phase: 'refused' }), true)
  assert.equal(refusalNeedsConsent({ error: 'hash_mismatch', message: '', consent: '', install_kind: 'frozen', swappable: true, phase: 'refused' }), false)
  assert.equal(refusalNeedsConsent({ error: 'source_install', message: '', install_kind: 'source', swappable: false, phase: 'refused' }), false)
  assert.equal(refusalNeedsConsent(null), false)
})

test('the progress line follows the phase and the bytes, and knows when it is over', () => {
  const at = (phase: string, extra: Record<string, unknown> = {}) =>
    installPhaseLabel({ install_kind: 'frozen', swappable: true, phase, ...extra })
  assert.equal(at('downloading', { bytes_downloaded: 32_400_000, bytes_total: 420_000_000 }), 'Downloading 32.4 MB of 420.0 MB')
  assert.equal(at('downloading', { bytes_downloaded: 1_500 }), 'Downloading 2 KB')
  assert.equal(at('downloading'), 'Downloading')
  assert.equal(at('verifying'), 'Verifying the download')
  assert.equal(at('preparing'), 'Unpacking the updater')
  assert.equal(at('handed_off'), 'Installing')
  assert.equal(at('a-phase-a-later-daemon-invented'), '')
  assert.equal(installPhaseLabel(null), '')
  for (const phase of ['handed_off', 'refused', 'failed']) assert.equal(installFinished(phase), true)
  for (const phase of ['idle', 'downloading', 'verifying', 'inspecting', 'preparing']) assert.equal(installFinished(phase), false)
  assert.equal(bytesLabel(2_100_000_000), '2.10 GB')
  assert.equal(bytesLabel(-1), '')
  assert.equal(bytesLabel(undefined), '')
})
