import assert from 'node:assert/strict'
import test from 'node:test'
import { blockers, isReady, type WslBridgeStatus, type WslDistro } from '../src/wslBridge.ts'

const OFF: WslBridgeStatus = { supported: true, enabled: false, distros: [] }
const ON: WslBridgeStatus = { supported: true, enabled: true, distros: [] }

const distro = (bridge?: Partial<WslDistro['bridge']>): WslDistro => ({
  name: 'Ubuntu',
  running: true,
  bridge: bridge
    ? {
        distro: 'Ubuntu',
        available: false,
        installed: false,
        reachable: null,
        harnesses: [],
        reasons: [],
        ...bridge,
      } as WslDistro['bridge']
    : undefined,
})

test('when the bridge is off, that is the only thing worth saying', () => {
  // Listing everything else a distribution lacks would bury the one action that
  // matters, and every other blocker is downstream of this switch anyway.
  const reasons = blockers(OFF, distro({ harnesses: [], reachable: false }))
  assert.equal(reasons.length, 1)
  assert.match(reasons[0], /off/i)
})

test('an unprobed distribution says so rather than claiming to be fine', () => {
  // The absence of findings is not a finding. Reporting "ready" here would be the
  // exact false-confidence this panel exists to remove.
  assert.match(blockers(ON, distro())[0], /Not checked/i)
  assert.equal(isReady(ON, distro()), false)
})

test('a missing native agent explains why a Windows one does not count', () => {
  // A Windows CLI on the WSL PATH through interop *runs*, which is what makes it
  // dangerous: the user sees a working agent whose transcript mux cannot follow.
  const reasons = blockers(ON, distro({ harnesses: [] }))
  assert.match(reasons.join(' '), /natively/i)
  assert.match(reasons.join(' '), /\/mnt/)
})

test('unreachable is reported separately from uninstalled, because the fixes differ', () => {
  const reasons = blockers(ON, distro({
    harnesses: [{ name: 'claude', executable: '/home/u/.local/bin/claude' }],
    reachable: false,
    installed: false,
  }))
  const joined = reasons.join(' ')
  assert.match(joined, /cannot reach the daemon/i)
  assert.match(joined, /not installed/i)
  // Reachability comes first: installing a bridge that still cannot phone home
  // fixes nothing, and sending someone to the firewall after a pointless install
  // is the wrong order.
  assert.ok(reasons.findIndex(r => /reach/i.test(r)) < reasons.findIndex(r => /installed/i.test(r)))
})

test('ready requires enabled, available and installed together', () => {
  const complete = distro({
    harnesses: [{ name: 'claude', executable: '/home/u/.local/bin/claude' }],
    available: true,
    installed: true,
    reachable: true,
  })
  assert.equal(isReady(ON, complete), true)
  // Off, an otherwise-complete distribution is still not ready: the daemon is not
  // listening for it.
  assert.equal(isReady(OFF, complete), false)
  assert.equal(blockers(ON, complete).length, 0)
})

test('an installed bridge that is unavailable falls back to the reported reasons', () => {
  const reasons = blockers(ON, distro({
    harnesses: [{ name: 'claude', executable: '/home/u/.local/bin/claude' }],
    installed: true,
    available: false,
    reachable: null,
    reasons: ['no python3 inside Ubuntu, which the bridge hook client needs'],
  }))
  assert.match(reasons.join(' '), /python3/)
})
