import assert from 'node:assert/strict'
import test from 'node:test'
import { buildFleetCommands, displayOrderKey, sessionVoiceAliases, type FleetCommandActions } from '../src/fleetCommands.ts'
import { runCommand } from '../src/commands.ts'
import type { Project, Session } from '../src/types.ts'

const session = (id: string, extra: Partial<Session> = {}) => ({
  id, name: id, project_id: 'p1', backend: 'codex', state: 'idle', last_activity_ts: 1,
  ...extra,
}) as unknown as Session

const project = (id: string, name = id) => ({ id, name, path: `/tmp/${id}`, layout: null, layout_revision: 1 }) as unknown as Project

const recordingActions = () => {
  const calls: string[] = []
  const actions: FleetCommandActions = {
    activateProject: id => { calls.push(`activate:${id}`) },
    focusProject: id => { calls.push(`focusProject:${id}`) },
    focusSession: target => { calls.push(`focusSession:${target.id}`) },
    spawnSession: async (id, backend, seedText) => { calls.push(`spawn:${id}:${backend}:${seedText ?? ''}`); return true },
  }
  return { calls, actions }
}

const build = (over: Partial<Parameters<typeof buildFleetCommands>[0]> = {}, actions?: FleetCommandActions) => buildFleetCommands({
  displayProjects: [project('p1', 'Alpha')],
  projects: [project('p1', 'Alpha')],
  sessions: [],
  activeProjectId: 'p1',
  backends: ['claude'],
  harnessDisplayName: backend => backend === 'claude' ? 'Claude' : backend,
  sessionName: target => target.name,
  isEnded: target => target.state === 'exited' || target.state === 'crashed',
  actions: actions ?? recordingActions().actions,
  ...over,
})

test('the numbered shortcuts follow sidebar order and refuse the Project already active', () => {
  const commands = build({
    displayProjects: [project('p1', 'Alpha'), project('p2', 'Beta')],
    projects: [project('p1', 'Alpha'), project('p2', 'Beta')],
  })
  const first = commands.find(command => command.id === 'project.activate(1)')
  const second = commands.find(command => command.id === 'project.activate(2)')
  assert.equal(first?.label, 'Switch to project 1: Alpha')
  assert.equal(first?.available, false)
  assert.equal(first?.disabledReason, 'Project is already active')
  assert.equal(second?.available, true)
})

test('only the first nine Projects get a numbered shortcut', () => {
  const many = Array.from({ length: 12 }, (_, index) => project(`p${index}`))
  const commands = build({ displayProjects: many, projects: many })
  assert.equal(commands.filter(command => command.id.startsWith('project.activate')).length, 9)
})

test('every command runs through the actions it was handed, not a captured closure', async () => {
  const { calls, actions } = recordingActions()
  const commands = build({ sessions: [session('s1')], activeProjectId: 'other' }, actions)
  runCommand(commands, 'project.activate(1)')
  runCommand(commands, 'project.focus:p1')
  runCommand(commands, 'session.focus:s1')
  runCommand(commands, 'session.spawn:p1:claude')
  await Promise.resolve()
  assert.deepEqual(calls, ['activate:p1', 'focusProject:p1', 'focusSession:s1', 'spawn:p1:claude:'])
})

test('a pending or ended session gets no focus command', () => {
  const commands = build({
    sessions: [session('live'), session('warming', { pending: true }), session('done', { state: 'exited' })],
  })
  const ids = commands.filter(command => command.id.startsWith('session.focus:')).map(command => command.id)
  assert.deepEqual(ids, ['session.focus:live'])
})

test('a spoken launch carries the seed text and reports what started', async () => {
  const { calls, actions } = recordingActions()
  const commands = build({}, actions)
  const launch = commands.find(command => command.id === 'session.spawn:p1:claude')
  assert.ok(launch?.voice?.phrases.includes('new Claude in Alpha'))
  // The active Project also answers to the bare phrase.
  assert.ok(launch?.voice?.phrases.includes('new Claude'))
  const result = await launch?.voice?.execute?.('write the tests')
  assert.deepEqual(calls, ['spawn:p1:claude:write the tests'])
  assert.equal(result?.detail, 'Started Claude in Alpha with the spoken seed. Still listening.')
})

test('a launch that fails says so rather than claiming a session', async () => {
  const actions: FleetCommandActions = {
    activateProject: () => {}, focusProject: () => {}, focusSession: () => {}, spawnSession: async () => false,
  }
  const commands = build({}, actions)
  const launch = commands.find(command => command.id === 'session.spawn:p1:claude')
  assert.equal((await launch?.voice?.execute?.(''))?.detail, 'The session could not be started. Still listening.')
})

test('the spoken Project number comes from sidebar order, not from the Project list', () => {
  const commands = build({
    displayProjects: [project('p2', 'Beta'), project('p1', 'Alpha')],
    projects: [project('p1', 'Alpha'), project('p2', 'Beta')],
  })
  const alpha = commands.find(command => command.id === 'session.spawn:p1:claude')
  assert.ok(alpha?.voice?.phrases.includes('new Claude in project 2'))
})

test('the sidebar order key changes with the order and with nothing else', () => {
  const alpha = project('p1'), beta = project('p2')
  assert.equal(displayOrderKey([alpha, beta]), displayOrderKey([project('p1'), project('p2')]))
  assert.notEqual(displayOrderKey([alpha, beta]), displayOrderKey([beta, alpha]))
  assert.equal(displayOrderKey([]), '')
})

test('a session is addressable by what it is waiting on', () => {
  const now = Math.floor(Date.now() / 1000)
  assert.deepEqual(sessionVoiceAliases(session('s', { awaiting_reason: 'approval' })), [
    'go to the one waiting for approval', 'show approvals', 'open approval',
  ])
  assert.deepEqual(sessionVoiceAliases(session('s', { awaiting_reason: 'rate_limit' })), ['go to the rate limited one'])
  assert.deepEqual(sessionVoiceAliases(session('s', { state: 'working', last_activity_ts: now })), ['go to the working one'])
  assert.deepEqual(sessionVoiceAliases(session('s', { state: 'idle' })), ['go to the idle one'])
  assert.deepEqual(sessionVoiceAliases(session('s', { state: 'exited' })), [])
})

test('a session working with no activity for five minutes becomes the stuck one', () => {
  const stale = session('s', { state: 'working', last_activity_ts: Math.floor(Date.now() / 1000) - 600 })
  assert.deepEqual(sessionVoiceAliases(stale), ['go to the stuck one'])
  const unknown = session('s', { state: 'idle', delivery_readiness: { state: 'unknown' } } as Partial<Session>)
  assert.deepEqual(sessionVoiceAliases(unknown), ['go to the stuck one'])
})
