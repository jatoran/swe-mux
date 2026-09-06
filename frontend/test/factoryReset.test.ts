import assert from 'node:assert/strict'
import test from 'node:test'
import {
  clearClientStorage, confirmationMatches, RESET_POLL_MS, RESET_SETTLE_MS, waitForDaemon,
} from '../src/factoryReset.ts'

// The origin-clearing half of a factory reset only exists in a browser, so the
// storage APIs are stubbed per-test rather than once at module scope: several
// of these tests need a *failing* store, and a shared stub would leak that
// failure into the sibling test files that share this process.
const globals = globalThis as unknown as Record<string, unknown>

type Store = { clear: () => void }

// Awaits the body before restoring. A synchronous `finally` around an async body
// puts the globals back while the body is still using them, which is how the
// first version of this quietly broke ten unrelated suites: they share this
// process, and a stub restored too early is a stub another file is holding.
async function withGlobals(patch: Record<string, unknown>, body: () => Promise<void>): Promise<void> {
  // `defineProperty` rather than assignment: `navigator` is a getter-only global
  // in Node, and a plain write throws before the body ever runs.
  const saved = new Map<string, PropertyDescriptor | undefined>()
  for (const key of Object.keys(patch)) {
    saved.set(key, Object.getOwnPropertyDescriptor(globals, key))
    Object.defineProperty(globals, key, { value: patch[key], configurable: true, writable: true })
  }
  try { await body() } finally {
    for (const [key, descriptor] of saved) {
      if (descriptor === undefined) delete globals[key]
      else Object.defineProperty(globals, key, descriptor)
    }
  }
}

function countingStore(): Store & { cleared: number } {
  const store = { cleared: 0, clear: () => { store.cleared += 1 } }
  return store
}

test('the confirmation phrase is matched case- and space-insensitively', () => {
  // The daemon compares the same way, so a UI that armed on a stricter rule
  // would refuse presses the daemon would have accepted - and one that armed on
  // a looser rule would send a request the daemon rejects, which reads to the
  // operator as the button being broken.
  assert.equal(confirmationMatches('factory reset', 'factory reset'), true)
  assert.equal(confirmationMatches('  Factory Reset  ', 'factory reset'), true)
  assert.equal(confirmationMatches('factoryreset', 'factory reset'), false)
  assert.equal(confirmationMatches('', 'factory reset'), false)
})

test('clearing the origin empties every store it can reach', async () => {
  const local = countingStore()
  const session = countingStore()
  const deleted: string[] = []
  const unregistered: number[] = []
  await withGlobals({
    localStorage: local,
    sessionStorage: session,
    caches: {
      keys: async () => ['assets-v1', 'shell'],
      delete: async (name: string) => { deleted.push(name); return true },
    },
    indexedDB: {
      databases: async () => [{ name: 'mux' }],
      deleteDatabase: (name: string) => {
        const request: Record<string, unknown> = { name }
        queueMicrotask(() => (request.onsuccess as () => void)?.())
        return request
      },
    },
    navigator: {
      serviceWorker: {
        getRegistrations: async () => [{ unregister: async () => { unregistered.push(1); return true } }],
      },
    },
  }, async () => {
    const failures = await clearClientStorage()
    assert.deepEqual(failures, [], 'a browser that answers every API reports no failures')
  })
  assert.equal(local.cleared, 1)
  assert.equal(session.cleared, 1)
  assert.deepEqual(deleted, ['assets-v1', 'shell'])
  assert.deepEqual(unregistered, [1])
})

test('a store that refuses is reported, and never stops the ones after it', async () => {
  // Private mode, a disabled IndexedDB and a locked cache all throw here, and
  // the reset has already happened on the daemon by the time this runs: a
  // thrown SecurityError that skipped the remaining steps - and the reload -
  // would strand the client on an install that no longer exists.
  const session = countingStore()
  await withGlobals({
    localStorage: { clear: () => { throw new Error('denied') } },
    sessionStorage: session,
    caches: undefined,
    indexedDB: undefined,
    navigator: {},
  }, async () => {
    const failures = await clearClientStorage()
    assert.deepEqual(failures, ['local storage'])
  })
  assert.equal(session.cleared, 1, 'the step after the failure still ran')
})

test('the successor is waited for past the predecessor, and gives up at the deadline', async () => {
  // The first probe must land after the settle delay: the old daemon is still
  // answering health for a moment after it accepts the reset, and reloading
  // into it would show the install that is about to be moved aside.
  const slept: number[] = []
  const sleep = async (ms: number) => { slept.push(ms) }
  let calls = 0
  const ok = await waitForDaemon(10_000, (async () => {
    calls += 1
    return { ok: calls > 2 } as Response
  }) as unknown as typeof fetch, sleep)
  assert.equal(ok, true)
  assert.equal(calls, 3)
  assert.equal(slept[0], RESET_SETTLE_MS)
  assert.deepEqual(slept.slice(1), [RESET_POLL_MS, RESET_POLL_MS])
})

test('a daemon that never returns is a false, not a hang', async () => {
  const ok = await waitForDaemon(-1, (async () => { throw new Error('down') }) as unknown as typeof fetch, async () => {})
  assert.equal(ok, false)
})
