import assert from 'node:assert/strict'
import test from 'node:test'
import {
  desktopHandoffEligible,
  followedLink,
  installSessionLinkInbox,
  runDesktopHandoff,
  SESSION_LINK_HOOK,
  SessionLinkInbox,
  shellLink,
  standaloneDisplay,
  withoutHandoff,
  type DesktopHandoffState,
  type HandoffDeps,
} from '../src/sessionLinks.ts'

const SESSION = 'f23a3d11-30db-4045-ad5a-09634d449639'
const HANDOFF = 'Zk3xY0abcdEFGHijklMNOPqrstuvWXyz'

test('an agent link names only a session, and is a link however the page was reached', () => {
  // Android points an open PWA window at the link, and what navigation type that reports
  // is the platform's business; a URL naming a session and no Project is never the app's
  // own projection, which always writes `project=`.
  for (const navigation of ['navigate', 'reload', 'back_forward', null]) {
    assert.deepEqual(followedLink(`?session=${SESSION}`, navigation), { sessionId: SESSION, handoff: null, source: 'url' })
  }
})

test('the app projecting its own URL is not a link, unless something navigated to it fresh', () => {
  const projected = `?project=work&session=${SESSION}`
  // A reload or a history step reads back what this tab wrote: per-device memory decides.
  assert.equal(followedLink(projected, 'reload'), null)
  assert.equal(followedLink(projected, 'back_forward'), null)
  // A fresh navigation to the same URL is someone following it.
  assert.deepEqual(followedLink(projected, 'navigate'), { sessionId: SESSION, handoff: null, source: 'url' })
})

test('a hand-off id is carried, and settles that the URL is a link', () => {
  assert.deepEqual(
    followedLink(`?project=work&session=${SESSION}&handoff=${HANDOFF}`, 'reload'),
    { sessionId: SESSION, handoff: HANDOFF, source: 'url' },
  )
  // A malformed hand-off is dropped rather than trusted; the session still opens.
  assert.deepEqual(followedLink(`?session=${SESSION}&handoff=short`, 'navigate'), { sessionId: SESSION, handoff: null, source: 'url' })
})

test('anything that is not a session id is not a link', () => {
  for (const search of ['', '?project=work', '?session=', '?session=..%2Fetc', '?session=a%20b', `?session=${'x'.repeat(200)}`]) {
    assert.equal(followedLink(search, 'navigate'), null, search)
  }
})

test('the shell hook accepts exactly a session id and an optional hand-off id', () => {
  assert.deepEqual(shellLink({ session_id: SESSION, handoff: HANDOFF }), { sessionId: SESSION, handoff: HANDOFF, source: 'shell' })
  assert.deepEqual(shellLink({ session_id: SESSION, handoff: null }), { sessionId: SESSION, handoff: null, source: 'shell' })
  assert.equal(shellLink({ session_id: SESSION, handoff: 'bad id!' }), null)
  assert.equal(shellLink({ session_id: '<script>' }), null)
  assert.equal(shellLink('session'), null)
  assert.equal(shellLink(null), null)
})

test('the hand-off id leaves the URL and nothing else does', () => {
  assert.equal(withoutHandoff(`http://127.0.0.1:8765/?session=${SESSION}&handoff=${HANDOFF}#x`), `/?session=${SESSION}#x`)
  assert.equal(withoutHandoff(`http://127.0.0.1:8765/?session=${SESSION}`), null)
})

function fakePage(search: string) {
  let href = `http://127.0.0.1:8765/${search}`
  const writes: string[] = []
  const win = {
    location: { get href() { return href }, get search() { return new URL(href).search } },
    history: { state: null, replaceState: (_state: unknown, _unused: string, url: string) => { writes.push(url); href = `http://127.0.0.1:8765${url}` } },
  }
  return { win, writes, href: () => href }
}

test('installing the inbox queues the page link, strips its hand-off id, and opens the hook', () => {
  const inbox = new SessionLinkInbox()
  const page = fakePage(`?session=${SESSION}&handoff=${HANDOFF}`)
  const target: Record<string, unknown> = {}
  installSessionLinkInbox(inbox, page.win, 'navigate', target)

  assert.deepEqual(inbox.initial, { sessionId: SESSION, handoff: HANDOFF, source: 'url' })
  assert.deepEqual(page.writes, [`/?session=${SESSION}`])

  let heard = 0
  inbox.subscribe(() => { heard += 1 })
  const hook = target[SESSION_LINK_HOOK] as (value: unknown) => boolean
  assert.equal(hook({ session_id: 'other', handoff: null }), true)
  assert.equal(hook({ session_id: '' }), false, 'a refused link is not queued')
  assert.equal(heard, 1)
  assert.deepEqual(inbox.drain().map(item => [item.sessionId, item.source]), [[SESSION, 'url'], ['other', 'shell']])
  assert.equal(inbox.size, 0)
  // The initial link stays readable after the drain: hydration asks about it.
  assert.equal(inbox.initial?.sessionId, SESSION)
})

test('a page that was not opened by a link queues nothing and rewrites nothing', () => {
  const inbox = new SessionLinkInbox()
  const page = fakePage(`?project=work&session=${SESSION}`)
  installSessionLinkInbox(inbox, page.win, 'reload', {})
  assert.equal(inbox.initial, null)
  assert.equal(inbox.size, 0)
  assert.deepEqual(page.writes, [])
})

test('only a Windows browser tab that a link opened is offered the desktop hand-off', () => {
  const base = { host: 'browser', platform: 'win', standalone: false, source: 'url' } as const
  assert.equal(desktopHandoffEligible(base), true)
  assert.equal(desktopHandoffEligible({ ...base, host: 'desktop' }), false, 'the desktop app never hands off to itself')
  assert.equal(desktopHandoffEligible({ ...base, platform: 'linux' }), false, 'Android reports linux')
  assert.equal(desktopHandoffEligible({ ...base, platform: 'mac' }), false)
  assert.equal(desktopHandoffEligible({ ...base, standalone: true }), false, 'an installed PWA is already an app')
  assert.equal(desktopHandoffEligible({ ...base, source: 'shell' }), false)
})

test('standalone display is any installed-app display mode', () => {
  assert.equal(standaloneDisplay(query => ({ matches: query === '(display-mode: standalone)' })), true)
  assert.equal(standaloneDisplay(() => ({ matches: false })), false)
  assert.equal(standaloneDisplay(undefined), false)
})

function handoffHarness(polls: Array<DesktopHandoffState | Error>, overrides: Partial<HandoffDeps> = {}) {
  let clock = 0
  const events: string[] = []
  const deps: HandoffDeps = {
    start: async () => ({ attempt: true, handoff_id: HANDOFF, url: `swe-mux://open?session=${SESSION}&handoff=${HANDOFF}` }),
    poll: async () => {
      const next = polls.shift() ?? { state: 'pending' }
      if (next instanceof Error) throw next
      return next
    },
    launch: url => { events.push(`launch ${url}`) },
    close: () => { events.push('close') },
    wait: async ms => { clock += ms },
    interacted: () => false,
    now: () => clock,
    note: () => {},
    ...overrides,
  }
  return { deps, events, elapsed: () => clock }
}

test('the tab closes once the desktop window reports it has the link', async () => {
  const harness = handoffHarness([{ state: 'pending' }, new Error('blip'), { state: 'opened', found: true }])
  const outcome = await runDesktopHandoff(SESSION, harness.deps, { pollMs: 500, budgetMs: 10_000, closeGraceMs: 400 })
  assert.deepEqual(harness.events, [`launch swe-mux://open?session=${SESSION}&handoff=${HANDOFF}`, 'close'])
  // Still running after close() means the browser refused it; the caller says so instead.
  assert.equal(outcome, 'opened_tab_kept')
})

test('a daemon that says not to try launches nothing', async () => {
  const harness = handoffHarness([], { start: async () => ({ attempt: false, reason: 'other_device' }) })
  assert.equal(await runDesktopHandoff(SESSION, harness.deps), 'not_attempted')
  assert.deepEqual(harness.events, [])
  const failing = handoffHarness([], { start: async () => { throw new Error('offline') } })
  assert.equal(await runDesktopHandoff(SESSION, failing.deps), 'failed_to_start')
  assert.deepEqual(failing.events, [])
})

test('every ending other than a confirmation leaves the tab open', async () => {
  const silent = handoffHarness([])
  assert.equal(await runDesktopHandoff(SESSION, silent.deps, { pollMs: 500, budgetMs: 3_000 }), 'timed_out')
  assert.ok(!silent.events.includes('close'))
  assert.ok(silent.elapsed() >= 3_000)

  const expired = handoffHarness([{ state: 'unknown' }])
  assert.equal(await runDesktopHandoff(SESSION, expired.deps), 'expired')
  assert.ok(!expired.events.includes('close'))

  // Someone started using the tab: an "opened" arriving afterwards must not close it.
  const used = handoffHarness([{ state: 'opened', found: true }], { interacted: () => true })
  assert.equal(await runDesktopHandoff(SESSION, used.deps), 'kept_by_interaction')
  assert.ok(!used.events.includes('close'))
})
