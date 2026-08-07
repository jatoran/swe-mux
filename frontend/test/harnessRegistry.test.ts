import assert from 'node:assert/strict'
import test from 'node:test'
import {
  allBackendNames,
  deliversHarnessPrompts,
  hasHarnessMeasurement,
  hasHarnessTranscript,
  hasExternalUsage,
  hasProviderAccounts,
  installHarnessRegistry,
  isAgentBackend,
  isObservedHarness,
} from '../src/harnessRegistry.ts'
import { sessionStatus } from '../src/sessionStatus.ts'
import type { Session } from '../src/types.ts'

const COMPATIBILITY_REGISTRY = {
  version: 1,
  harnesses: [
    { name: 'claude', display_name: 'Claude Code', level: 'managed' as const, state_sources: ['transcript', 'hook', 'pty', 'cli_state'], measurement_source: 'transcript' as const, capabilities: { observed: true, transcript: true, measurement: true, lifecycle_hooks: true, pty_delivery: true, external_usage: true, provider_accounts: true } },
    { name: 'codex', display_name: 'Codex', level: 'managed' as const, state_sources: ['transcript', 'hook', 'pty'], measurement_source: 'transcript' as const, capabilities: { observed: true, transcript: true, measurement: true, lifecycle_hooks: true, pty_delivery: true, external_usage: true, provider_accounts: true } },
    { name: 'omp', display_name: 'oh-my-pi', level: 'managed' as const, state_sources: ['hook', 'transcript', 'pty'], measurement_source: 'transcript' as const, capabilities: { observed: true, transcript: true, measurement: true, lifecycle_hooks: true, pty_delivery: true, external_usage: false, provider_accounts: false } },
  ],
}

test('a launchable harness gets terminal affordances without invented observation data', () => {
  installHarnessRegistry({
    version: 1,
    harnesses: [{
      name: 'future-agent',
      display_name: 'Future Agent',
      level: 'launchable',
      state_sources: [],
      measurement_source: 'none',
      capabilities: { observed: false, transcript: false, measurement: false, lifecycle_hooks: false, pty_delivery: true, external_usage: false, provider_accounts: false },
    }],
  })

  assert.equal(isAgentBackend('future-agent'), true)
  assert.equal(deliversHarnessPrompts('future-agent'), true)
  assert.equal(isObservedHarness('future-agent'), false)
  assert.equal(hasHarnessTranscript('future-agent'), false)
  assert.equal(hasHarnessMeasurement('future-agent'), false)
  assert.equal(hasExternalUsage('future-agent'), false)
  assert.equal(hasProviderAccounts('future-agent'), false)
  assert.deepEqual(allBackendNames(), ['shell', 'future-agent'])
  assert.equal(sessionStatus({ backend: 'future-agent', state: 'running' } as Session), 'not observed by mux')

  installHarnessRegistry(COMPATIBILITY_REGISTRY)
  assert.equal(hasExternalUsage('claude'), true)
  assert.equal(hasProviderAccounts('codex'), true)
  assert.equal(hasExternalUsage('omp'), false)
  assert.equal(hasProviderAccounts('omp'), false)
})
