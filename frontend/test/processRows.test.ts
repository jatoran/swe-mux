import assert from 'node:assert/strict'
import test from 'node:test'
import {
  commandTail, isAbnormalState, processDetails, processMetrics, processRowKey, processState,
  rollupLabel, sessionRollup,
} from '../src/processRows.ts'

const base = {
  pid: 89460,
  parent_pid: 41280,
  executable: 'claude.exe',
  command: 'claude.exe --session-id d9269550 --mcp-config C:\\Users\\x\\.mux\\claude-mcp.json',
  cpu_pct: 2.5,
  memory_bytes: 598736896,
}

const at = (epoch: number) => `t${epoch}`

test('the row strips the executable it already names from the command tail', () => {
  assert.equal(commandTail(base), '--session-id d9269550 --mcp-config C:\\Users\\x\\.mux\\claude-mcp.json')
})

test('a quoted or fully-qualified launch path still resolves to its executable', () => {
  assert.equal(commandTail({ ...base, command: '"C:\\Program Files\\node\\node.exe" server.js --port 3000', executable: 'node.exe' }),
    'server.js --port 3000')
  assert.equal(commandTail({ ...base, command: 'C:/tools/claude.exe --resume', executable: 'claude.exe' }), '--resume')
})

test('a command that does not start with its own executable is left whole', () => {
  assert.equal(commandTail({ ...base, command: 'npm run dev', executable: 'node.exe' }), 'npm run dev')
})

test('an unreadable command line falls back to its durable fingerprint', () => {
  assert.equal(commandTail({ ...base, command: '', command_hash: 'ab12cd34ef567890' }), 'fingerprint ab12cd34ef56')
  assert.equal(commandTail({ ...base, command: '' }), 'command unavailable')
})

test('metrics name the network only when there is network to name', () => {
  assert.equal(processMetrics(base), '2.5% · 571.0 MiB')
  assert.equal(processMetrics({ ...base, connections: [{ remote_host: '1.1.1.1', remote_port: 443 }] }), '2.5% · 571.0 MiB · 1C')
  assert.equal(
    processMetrics({ ...base, listeners: [{ url: 'http://127.0.0.1:3000/', port: 3000 }], connections: [{ remote_host: '1.1.1.1', remote_port: 443 }] }),
    '2.5% · 571.0 MiB · 1L/1C',
  )
})

test('only unexpected states earn a badge beside the row', () => {
  assert.equal(isAbnormalState('active'), false)
  assert.equal(isAbnormalState('exited'), false)
  assert.equal(isAbnormalState('suspected_orphan'), true)
  assert.equal(isAbnormalState('escaped'), true)
})

test('state falls back to whether the process has exited', () => {
  assert.equal(processState(base), 'active')
  assert.equal(processState({ ...base, exited_at: 100 }), 'exited')
  assert.equal(processState({ ...base, exited_at: 100, evidence_state: 'stale' }), 'stale')
})

test('a row key survives a refresh that replaces every object', () => {
  assert.equal(processRowKey({ ...base, identity_id: 'ident-1', started_at: 5 }), 'ident-1')
  assert.equal(processRowKey({ ...base, started_at: 5 }), '89460:5')
})

test('details carry every field the collapsed row dropped', () => {
  const details = processDetails({
    ...base,
    evidence_reason: 'live_descendant_fingerprint_match',
    confidence: 'high',
    attribution_source: 'session_root',
    attribution_version: 2,
    job_assignment: 'daemon_job_assigned',
    last_verified_at: 10,
    first_seen: 1,
    last_seen: 9,
    listeners: [{ url: 'http://127.0.0.1:3000/', port: 3000 }],
    connections: [{ remote_host: '1.1.1.1', remote_port: 443 }],
    conditions: ['high memory'],
  }, at)
  const byLabel = new Map(details.map(detail => [detail.label, detail.value]))
  assert.equal(byLabel.get('command'), base.command)
  assert.equal(byLabel.get('parent'), 'PID 41280')
  assert.equal(byLabel.get('evidence'), 'live_descendant_fingerprint_match · confidence high')
  assert.equal(byLabel.get('attribution'), 'session_root v2 · daemon_job_assigned')
  assert.equal(byLabel.get('checked'), 't10')
  assert.equal(byLabel.get('seen'), 'first t1 · last t9')
  assert.equal(byLabel.get('network'), 'listening http://127.0.0.1:3000/ · connected 1.1.1.1:443')
  assert.equal(byLabel.get('warnings'), 'high memory')
})

test('details omit rather than blank the fields a process does not have', () => {
  const labels = processDetails(base, at).map(detail => detail.label)
  assert.deepEqual(labels, ['command', 'parent', 'evidence', 'attribution'])
})

test('long network lists are sampled rather than printed whole', () => {
  const connections = Array.from({ length: 7 }, (_, index) => ({ remote_host: '10.0.0.1', remote_port: 400 + index }))
  const network = processDetails({ ...base, connections }, at).find(detail => detail.label === 'network')
  assert.equal(network?.value, 'connected 10.0.0.1:400, 10.0.0.1:401, 10.0.0.1:402, 10.0.0.1:403 +3 more')
})

test('a session rollup that would only restate its single row is suppressed', () => {
  assert.equal(sessionRollup([base]), null)
  assert.equal(sessionRollup([base, { ...base, pid: 2, exited_at: 5 }]), null)
  assert.equal(sessionRollup([]), null)
})

test('a rollup that actually aggregates keeps its numbers', () => {
  const rollup = sessionRollup([base, { ...base, pid: 2, cpu_pct: 1.5, connections: [{ remote_host: 'a', remote_port: 1 }] }])
  assert.deepEqual(rollup, { processes: 2, cpu_pct: 4, memory_bytes: 1197473792, listeners: 0, connections: 1 })
  assert.equal(rollupLabel(rollup!), '2 proc · CPU 4.0% · 1.1 GiB · 1C')
})
