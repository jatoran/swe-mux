import assert from 'node:assert/strict'
import test from 'node:test'
import { HARNESS_REGISTRY_SEED } from '../src/harnessRegistrySeed.ts'
import { installHarnessRegistry } from '../src/harnessRegistry.ts'
import { inputBackendIsAgent, resolveInputBackend } from '../src/inputBackend.ts'
import { consoleContentionNotice, agentOutlivesPane } from '../src/consoleContention.ts'
import type { Session } from '../src/types'

installHarnessRegistry(HARNESS_REGISTRY_SEED)

const session = (overrides: Partial<Session>): Session =>
  ({ backend: 'shell', ...overrides }) as Session

test('a promoted pane keeps answering for its own backend', () => {
  assert.equal(resolveInputBackend(session({ backend: 'claude' })), 'claude')
  assert.equal(
    // Once promoted, a stale pending list must not be able to override the answer.
    resolveInputBackend(session({ backend: 'codex', agent_launch_pending: ['claude'] })),
    'codex',
  )
})

test('a plain shell with nothing pending stays a shell', () => {
  assert.equal(resolveInputBackend(session({})), 'shell')
  assert.equal(resolveInputBackend(session({ agent_launch_pending: [] })), 'shell')
  assert.equal(inputBackendIsAgent(session({})), false)
})

test('an unpromoted shell adopts the single harness it has seen launching', () => {
  const pane = session({ agent_launch_pending: ['claude'] })
  assert.equal(resolveInputBackend(pane), 'claude')
  assert.equal(inputBackendIsAgent(pane), true)
})

test('two candidates are refused rather than guessed between', () => {
  // The daemon's own promotion refuses an ambiguous match for the same reason:
  // picking one would apply a measured harness's byte sequences to a different
  // harness's composer.
  const pane = session({ agent_launch_pending: ['claude', 'codex'] })
  assert.equal(resolveInputBackend(pane), 'shell')
  assert.equal(inputBackendIsAgent(pane), false)
})

test('a name that is not a known harness is ignored', () => {
  assert.equal(resolveInputBackend(session({ agent_launch_pending: ['notaharness'] })), 'shell')
  assert.equal(
    resolveInputBackend(session({ agent_launch_pending: ['notaharness', 'claude'] })),
    'claude',
  )
})

test('no contention means no notice', () => {
  assert.equal(consoleContentionNotice(session({})), null)
  assert.equal(consoleContentionNotice(session({ console_contention: null })), null)
  assert.equal(agentOutlivesPane(session({})), false)
})

test('a contention notice states the split rather than naming mux internals', () => {
  const notice = consoleContentionNotice(
    session({ console_contention: { reason: 'shell_regained_console', since: 1 } }),
  )
  if (!notice) throw new Error('expected a notice')
  assert.match(notice.text, /shell/)
  assert.doesNotMatch(notice.text, /shim|wrapper|swe-mux\.exe/i)
  // The repair for the ordinary case is to restart the agent from the Run menu,
  // which spawns it straight into the terminal with no launch chain at all.
  assert.match(notice.hint, /Run menu/)
})

test('an orphaned agent is told it will survive the pane', () => {
  for (const reason of ['agent_orphaned', 'shim_exited_first'] as const) {
    const notice = consoleContentionNotice(session({ console_contention: { reason, since: 1 } }))
    if (!notice) throw new Error('expected a notice')
    assert.match(notice.hint, /will not stop the agent/)
    assert.equal(agentOutlivesPane(session({ console_contention: { reason, since: 1 } })), true)
  }
})

test('a census placing the agent outside the tree also means it outlives the pane', () => {
  const pane = session({
    console_contention: {
      reason: 'shell_regained_console',
      since: 1,
      census: { root_pid: 1, agent_pid: 2, agent_alive: true, agent_in_pty_tree: false },
    },
  })
  assert.equal(agentOutlivesPane(pane), true)
})

test('an agent still inside the tree does not claim to outlive the pane', () => {
  const pane = session({
    console_contention: {
      reason: 'shell_regained_console',
      since: 1,
      census: { root_pid: 1, agent_pid: 2, agent_alive: true, agent_in_pty_tree: true },
    },
  })
  assert.equal(agentOutlivesPane(pane), false)
})
