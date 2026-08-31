import assert from 'node:assert/strict'
import test from 'node:test'
import {
  DESKTOP_MARKER, desktopShellReport, detectPlatform, hostQuery, keyboardLockAvailable,
  resetHostProfile,
} from '../src/hostProfile.ts'
import { correctedUnreachable, coverage, emptyReport, probeCandidates, recordBlocked } from '../src/hostKeyboardProbe.ts'

test('the absence of the marker is the signal, not a missing value', () => {
  // A browser tab never has this global. That is how the two hosts are told apart,
  // and it is why nothing here invents a default when it is missing.
  assert.equal(desktopShellReport({}), null)
  assert.equal(desktopShellReport({ [DESKTOP_MARKER]: 'yes' }), null)
  assert.equal(desktopShellReport({ [DESKTOP_MARKER]: { shell: 'something-else' } }), null)
  assert.deepEqual(
    desktopShellReport({ [DESKTOP_MARKER]: { shell: 'swe-mux-desktop', version: '0.1.3', accelerators: 'released' } }),
    { version: '0.1.3', accelerators: 'released' },
  )
})

test('an unrecognised platform answers linux, which is the conservative one', () => {
  // Its window manager grabs the most chords, so an unknown host is told about more
  // conflicts rather than fewer.
  assert.equal(detectPlatform({ userAgentData: { platform: 'Windows' } }), 'win')
  assert.equal(detectPlatform({ platform: 'MacIntel' }), 'mac')
  assert.equal(detectPlatform({ userAgent: 'iPhone' }), 'mac')
  assert.equal(detectPlatform({ userAgent: 'X11; Fedora' }), 'linux')
  assert.equal(detectPlatform({}), 'linux')
  // Not `detectPlatform(undefined)`: that reads the real `navigator`, so the answer
  // is a property of whichever machine runs the suite - `win` here, `linux` on CI.
  // Asserting one of them would be asserting the host, which is the failure mode
  // CI taught this repository in the week of 2026-08-27.
  assert.ok(['win', 'mac', 'linux'].includes(detectPlatform(undefined)))
})

test('keyboard lock is detected rather than assumed', () => {
  assert.ok(keyboardLockAvailable({ keyboard: { lock: () => {} } }))
  assert.ok(!keyboardLockAvailable({ keyboard: {} }))
  assert.ok(!keyboardLockAvailable(undefined))
})

test('the host descriptor is what the daemon resolves against', () => {
  resetHostProfile({ host: 'desktop', platform: 'mac', shellVersion: '0.1.3', keyboardLockAvailable: false })
  assert.equal(hostQuery(), 'host=desktop&platform=mac')
  resetHostProfile()
})

test('an untested chord is unknown, never blocked', () => {
  // Absence of a keydown and absence of a keypress are the same signal from outside.
  const candidates = probeCandidates({ browser_unreachable: ['ctrl+t'], browser_contested: { 'ctrl+f': 'find' } })
  assert.deepEqual(candidates, ['ctrl+f', 'ctrl+t'])
  const report = emptyReport('browser', 'win', candidates)
  assert.deepEqual(coverage(report), { tested: 0, total: 2 })
  assert.equal(report.results['ctrl+t'].verdict, 'unknown')
})

test('only a tested chord moves the shipped verdict', () => {
  const report = emptyReport('browser', 'win', ['ctrl+t', 'ctrl+w'])
  report.results['ctrl+t'] = { chord: 'ctrl+t', verdict: 'delivered', at: 1 }
  const { unreachable, corrections } = correctedUnreachable(['ctrl+t', 'ctrl+w'], report)
  // `ctrl+t` was measured as deliverable here, so it is handed back; `ctrl+w` was
  // never tried, so the table's answer stands rather than being quietly relaxed.
  assert.deepEqual([...unreachable], ['ctrl+w'])
  assert.deepEqual(corrections, ['ctrl+t'])
})

test('a measured block is added even where the table did not expect one', () => {
  const report = recordBlocked(emptyReport('browser', 'win', ['ctrl+f']), 'ctrl+f')
  const { unreachable, corrections } = correctedUnreachable([], report)
  assert.deepEqual([...unreachable], ['ctrl+f'])
  assert.deepEqual(corrections, ['ctrl+f'])
})

test('no report at all leaves the shipped table exactly as it was', () => {
  const { unreachable, corrections } = correctedUnreachable(['ctrl+t'], null)
  assert.deepEqual([...unreachable], ['ctrl+t'])
  assert.deepEqual(corrections, [])
})
