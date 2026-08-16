import assert from 'node:assert/strict'
import test from 'node:test'
import {
  allBackendNames,
  allHarnessesIncludingDisabled,
  applicationTouchScrollProfile,
  appliesWidthEnvelope,
  assignsConversationId,
  deliversHarnessPrompts,
  enabledHarnessNames,
  harnessDisplayName,
  harnessEnabled,
  hasHarnessMeasurement,
  hasHarnessTranscript,
  hasProviderAccounts,
  installHarnessRegistry,
  isAgentBackend,
  isObservedHarness,
  minDesktopColumns,
  ownsScrollViewport,
  promptDeliveryHarnesses,
  setHarnessEnablement,
  skillInvocationPrefix,
  supportsBranch,
  suppressesLateColorResponse,
  transcriptHarnesses,
  webglUnsafe,
} from '../src/harnessRegistry.ts'
import { HARNESS_REGISTRY_SEED } from '../src/harnessRegistrySeed.ts'
import { railPayload } from '../src/commandRail.ts'
import { resumeCommand } from '../src/resumeCommand.ts'
import { shouldLoadWebgl } from '../src/terminalRenderer.ts'
import { sessionStatus } from '../src/sessionStatus.ts'
import type { Session } from '../src/types.ts'

/** Restore the generated seed, which is the daemon's own view of the registry. */
const seed = () => installHarnessRegistry(structuredClone(HARNESS_REGISTRY_SEED))

test('a launchable harness gets terminal affordances without invented observation data', () => {
  installHarnessRegistry({
    version: 1,
    harnesses: [{
      name: 'future-agent',
      display_name: 'Future Agent',
      level: 'launchable',
      state_sources: [],
      measurement_source: 'none',
      capabilities: { observed: false, transcript: false, measurement: false, lifecycle_hooks: false, pty_delivery: true, provider_accounts: false },
    }],
  })

  assert.equal(isAgentBackend('future-agent'), true)
  assert.equal(deliversHarnessPrompts('future-agent'), true)
  assert.equal(isObservedHarness('future-agent'), false)
  assert.equal(hasHarnessTranscript('future-agent'), false)
  assert.equal(hasHarnessMeasurement('future-agent'), false)
  assert.equal(hasProviderAccounts('future-agent'), false)
  assert.deepEqual(allBackendNames(), ['shell', 'future-agent'])
  assert.equal(sessionStatus({ backend: 'future-agent', state: 'running' } as Session), 'not observed by mux')

  seed()
  assert.equal(hasProviderAccounts('codex'), true)
  assert.equal(hasProviderAccounts('omp'), false)
})

test('an unknown harness defaults every terminal trait to the conservative answer', () => {
  // A daemon older than this build sends no such fields, and a harness it has never
  // heard of has no descriptor at all. Neither may be read as "safe".
  installHarnessRegistry({
    version: 1,
    harnesses: [{
      name: 'future-agent',
      display_name: 'Future Agent',
      level: 'observed',
      state_sources: ['hook'],
      measurement_source: 'none',
      capabilities: { observed: true, transcript: false, measurement: false, lifecycle_hooks: true, pty_delivery: true, provider_accounts: false },
    }],
  })

  // WebGL and scrollback repaint both fail closed: the DOM renderer is the one with
  // no failure mode, so an undeclared harness never reaches WebGL by either route.
  assert.equal(webglUnsafe('future-agent'), true)
  assert.equal(shouldLoadWebgl('auto', false, 'future-agent'), false)
  assert.equal(shouldLoadWebgl('webgl', false, 'future-agent'), false)
  // A shell declares nothing and stays eligible: it is not an agent surface.
  assert.equal(webglUnsafe('shell'), false)
  assert.equal(shouldLoadWebgl('auto', false, 'shell'), true)

  // Behavioural opt-ins default off: an undeclared harness gets no width envelope,
  // no font floor, no scroll-viewport claim, no response filtering, no branch.
  assert.equal(appliesWidthEnvelope('future-agent'), false)
  assert.equal(minDesktopColumns('future-agent'), 0)
  assert.equal(ownsScrollViewport('future-agent'), false)
  assert.equal(suppressesLateColorResponse('future-agent'), false)
  assert.equal(supportsBranch('future-agent'), false)
  assert.equal(assignsConversationId('future-agent'), false)
  assert.deepEqual(applicationTouchScrollProfile('future-agent'), { rowsPerReport: 1 })
  // The majority spelling, because typing `/name` is the least surprising guess.
  assert.equal(skillInvocationPrefix('future-agent'), '/')

  seed()
})

test('the generated seed carries the daemon capabilities the browser gates on', () => {
  seed()

  // The drift this seed exists to prevent: opencode reaches `managed` on hooks plus
  // database measurement, and was hand-seeded two levels lower with measurement off.
  const opencode = HARNESS_REGISTRY_SEED.harnesses.find(item => item.name === 'opencode')
  assert.equal(opencode?.level, 'managed')
  assert.equal(opencode?.measurement_source, 'database')
  assert.equal(hasHarnessMeasurement('opencode'), true)
  // pi keeps a PTY reading, which the hand-written seed had dropped.
  const pi = HARNESS_REGISTRY_SEED.harnesses.find(item => item.name === 'pi')
  assert.deepEqual(pi?.state_sources, ['hook', 'transcript', 'pty'])

  assert.equal(webglUnsafe('claude'), true)
  assert.equal(webglUnsafe('omp'), true)
  assert.equal(webglUnsafe('codex'), false)
  assert.equal(shouldLoadWebgl('webgl', false, 'omp'), false)
  assert.equal(shouldLoadWebgl('webgl', false, 'codex'), true)
  assert.equal(shouldLoadWebgl('auto', false, 'codex'), false)

  assert.equal(ownsScrollViewport('claude'), true)
  assert.equal(ownsScrollViewport('codex'), false)
  assert.equal(appliesWidthEnvelope('claude'), true)
  assert.equal(minDesktopColumns('codex'), 80)
  assert.equal(minDesktopColumns('claude'), 0)
  assert.equal(suppressesLateColorResponse('codex'), true)
  assert.equal(suppressesLateColorResponse('claude'), false)
  assert.deepEqual(applicationTouchScrollProfile('claude'), { rowsPerReport: 3 })
  assert.deepEqual(applicationTouchScrollProfile('codex'), { rowsPerReport: 1 })
  assert.deepEqual(
    HARNESS_REGISTRY_SEED.harnesses.filter(item => item.capabilities.branch).map(item => item.name),
    ['claude', 'codex'],
  )
})

test('enablement is a launcher filter that never reaches display, transcript, or history surfaces', () => {
  const caps = (extra: Record<string, boolean> = {}) => ({
    observed: true, transcript: true, measurement: true, lifecycle_hooks: true,
    pty_delivery: true, provider_accounts: false, ...extra,
  })
  // codex is genuinely absent from this machine; claude and omp are present.
  installHarnessRegistry({
    version: 1,
    harnesses: [
      { name: 'claude', display_name: 'Claude Code', installed: true, resolved_path: '/x/claude', level: 'managed', state_sources: ['hook'], measurement_source: 'transcript', capabilities: caps() },
      { name: 'codex', display_name: 'Codex', installed: false, resolved_path: null, level: 'managed', state_sources: ['hook'], measurement_source: 'transcript', capabilities: caps() },
      { name: 'omp', display_name: 'oh-my-pi', installed: true, resolved_path: '/x/omp', level: 'managed', state_sources: ['hook'], measurement_source: 'transcript', capabilities: caps() },
    ],
  })

  // No explicit choices: enablement follows detection, so an uninstalled harness is
  // hidden from the launchers.
  setHarnessEnablement({})
  assert.deepEqual(allBackendNames(), ['shell', 'claude', 'omp'])
  assert.deepEqual(enabledHarnessNames(), ['claude', 'omp'])
  assert.deepEqual(promptDeliveryHarnesses().map(h => h.name), ['claude', 'omp'])
  assert.equal(harnessEnabled('codex'), false)

  // The main risk: a disabled harness must still render everywhere that is not a
  // launcher. Its display name, transcript parsing, and history rows keep seeing it.
  assert.equal(harnessDisplayName('codex'), 'Codex')
  assert.deepEqual(transcriptHarnesses().map(h => h.name), ['claude', 'codex', 'omp'])
  assert.deepEqual(allHarnessesIncludingDisabled().map(h => h.name), ['claude', 'codex', 'omp'])

  // An explicit choice wins in both directions: force an absent harness on, and hide
  // an installed one the user never uses.
  setHarnessEnablement({ codex: true, claude: false })
  assert.deepEqual(enabledHarnessNames(), ['codex', 'omp'])
  assert.equal(harnessEnabled('claude'), false)
  // ...but display and history are still unfiltered.
  assert.deepEqual(transcriptHarnesses().map(h => h.name), ['claude', 'codex', 'omp'])

  // First paint: the static seed carries no `installed`, which reads as enabled so
  // the very first render never hides a harness the user actually has.
  setHarnessEnablement({})
  installHarnessRegistry({
    version: 1,
    harnesses: [{ name: 'codex', display_name: 'Codex', level: 'managed', state_sources: ['hook'], measurement_source: 'transcript', capabilities: caps() }],
  })
  assert.equal(harnessEnabled('codex'), true)

  setHarnessEnablement({})
  seed()
})

test('a skill rail item uses each harness own invocation spelling', () => {
  seed()

  // The divergence this replaced: the rail knew `$` for Codex and `/` for everything
  // else, so an oh-my-pi pane was typed `/commit` where its CLI wants `/skill:commit`.
  const item = { id: 'x', type: 'skill' as const, text: 'commit', label: 'Commit', submit: true }
  assert.equal(railPayload(item, 'claude'), '/commit')
  assert.equal(railPayload(item, 'codex'), '$commit')
  assert.equal(railPayload(item, 'omp'), '/skill:commit')
  assert.equal(railPayload(item, 'pi'), '/commit')
  assert.equal(railPayload(item, 'opencode'), '/commit')

  // An item authored with a spelling already applied keeps it rather than stacking.
  assert.equal(railPayload({ ...item, text: '/skill:commit' }, 'omp'), '/skill:commit')
  assert.equal(railPayload({ ...item, text: '$commit' }, 'codex'), '$commit')
  // Slash commands are literal everywhere and never take the skill prefix.
  assert.equal(railPayload({ ...item, type: 'slash' }, 'omp'), '/commit')
})

test('resume commands are composed from each harness declared grammar', () => {
  seed()

  // Claude is resumable at once: mux minted the id, so it equals the session id.
  assert.equal(
    resumeCommand({ id: 'abc', backend: 'claude', native_session_id: 'abc' }),
    'claude --resume abc',
  )
  // The others carry a placeholder equal to the mux id until one is discovered.
  assert.equal(resumeCommand({ id: 'abc', backend: 'codex', native_session_id: 'abc' }), null)
  assert.equal(
    resumeCommand({ id: 'abc', backend: 'codex', native_session_id: 'thread-1' }),
    'codex resume thread-1',
  )
  assert.equal(
    resumeCommand({ id: 'abc', backend: 'omp', native_session_id: 'omp-1' }),
    'omp --resume omp-1',
  )
  // pi and opencode had no copyable resume command at all before the grammar was
  // declared, despite both resuming by id through `--session`.
  assert.equal(
    resumeCommand({ id: 'abc', backend: 'pi', native_session_id: 'pi-1' }),
    'pi --session pi-1',
  )
  assert.equal(
    resumeCommand({ id: 'abc', backend: 'opencode', native_session_id: 'ses_1' }),
    'opencode --session ses_1',
  )
  // A shell and an unknown harness have no conversation to resume.
  assert.equal(resumeCommand({ id: 'abc', backend: 'shell', native_session_id: 'abc' }), null)
  assert.equal(resumeCommand({ id: 'abc', backend: 'nope', native_session_id: 'x' }), null)
})
