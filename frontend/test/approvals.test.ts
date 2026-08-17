import assert from 'node:assert/strict'
import test from 'node:test'
import {
  APPROVAL_MODES,
  MODE_LABELS,
  approvalLapse,
  approvalSummary,
  effectiveApprovalMode,
  modeUnavailableReason,
  showsApprovalBadge,
} from '../src/approvals.ts'
import type { ApprovalPolicy, ApprovalStatus } from '../src/types.ts'

const NOW = 1_760_000_000

const policy = (over: Partial<ApprovalPolicy> = {}): ApprovalPolicy => ({
  mode: 'allow_all',
  run_id: 'run-1',
  expires_at: NOW + 600,
  granted_at: NOW - 60,
  set_by: 'pane',
  rules: [],
  auto_approved: 3,
  max_auto: 200,
  last_decision_at: NOW - 10,
  last_request: 'Read(/repo/x.ts)',
  floor_deferred: 0,
  ...over,
})

const status = (over: Partial<ApprovalStatus> = {}): ApprovalStatus => ({
  supported: true,
  enabled: true,
  ceiling: 'allow_all',
  rules: ['Read'],
  rules_source: 'default',
  unavailable: null,
  ttl_seconds: 1800,
  max_auto: 200,
  policy: policy(),
  effective_mode: 'allow_all',
  modes: APPROVAL_MODES,
  ...over,
})

test('a session with no grant is simply wait', () => {
  assert.equal(effectiveApprovalMode({ approval_policy: undefined }, NOW), 'wait')
  assert.equal(showsApprovalBadge({ approval_policy: undefined }, NOW), false)
})

test('an expired grant applies as wait, and says it expired rather than off', () => {
  const expired = policy({ expires_at: NOW - 1 })
  assert.equal(approvalLapse(expired, 'run-1', NOW), 'expired')
  assert.equal(effectiveApprovalMode({ approval_policy: expired, agent_run_id: 'run-1' }, NOW), 'wait')
  assert.match(approvalSummary(status({ policy: expired }), NOW), /expired/)
})

test('a grant does not follow the session into a replaced conversation', () => {
  // /clear, /resume, Branch and rollover all mint a new agent_run_id. Authority
  // granted for one task must not silently apply to the next.
  const held = policy()
  assert.equal(approvalLapse(held, 'run-2', NOW), 'superseded')
  assert.equal(effectiveApprovalMode({ approval_policy: held, agent_run_id: 'run-2' }, NOW), 'wait')
  assert.match(approvalSummary(status({ policy: held }), NOW), /allow all/)
})

test('a grant made against no conversation never applies', () => {
  assert.equal(approvalLapse(policy({ run_id: null }), 'run-1', NOW), 'superseded')
})

test('a spent budget is reported as spent, not as off', () => {
  // "It stopped answering and I do not know why" is the reading that makes an
  // operator distrust the feature.
  const spent = policy({ auto_approved: 200, max_auto: 200 })
  assert.equal(approvalLapse(spent, 'run-1', NOW), 'exhausted')
  assert.match(approvalSummary(status({ policy: spent }), NOW), /budget spent/)
})

test('the summary reports how much authority stands and how much was spent', () => {
  const line = approvalSummary(status(), NOW)
  assert.match(line, /allow all/)
  assert.match(line, /3\/200 approved/)
  assert.match(line, /10m left/)
})

test('floor deferrals are surfaced so "it still asked me" is not read as a bug', () => {
  const line = approvalSummary(status({ policy: policy({ floor_deferred: 2 }) }), NOW)
  assert.match(line, /2 held for you/)
})

test('an install with the feature off says so before anything else', () => {
  assert.equal(approvalSummary(status({ enabled: false }), NOW), 'off for this install')
})

test('an unsupported harness is named as unsupported rather than as wait', () => {
  assert.equal(approvalSummary(status({ supported: false }), NOW), 'unsupported here')
})

test('wait carries the reason it cannot be changed, when there is one', () => {
  const line = approvalSummary(
    status({ policy: policy({ mode: 'wait' }), unavailable: 'no agent conversation is running here' }),
    NOW,
  )
  assert.match(line, /^wait · no agent conversation/)
})

test('a mode above the Project ceiling is refused in the UI with the ceiling named', () => {
  const capped = status({ ceiling: 'allowlisted' })
  assert.match(modeUnavailableReason(capped, 'allow_all'), /ceiling is allowlisted/)
  assert.equal(modeUnavailableReason(capped, 'allowlisted'), '')
})

test('an empty allowlist blocks allowlisted rather than granting nothing', () => {
  assert.match(modeUnavailableReason(status({ rules: [] }), 'allowlisted'), /allowlist is empty/)
})

test('returning to wait is never blocked', () => {
  const worst = status({ enabled: false, ceiling: 'wait', rules: [], unavailable: 'off for this install' })
  assert.equal(modeUnavailableReason(worst, 'wait'), '')
})

test('the mode ladder is ordered weakest first', () => {
  // `modeUnavailableReason` compares by index, so the order is load-bearing
  // rather than cosmetic.
  assert.deepEqual(APPROVAL_MODES, ['wait', 'allowlisted', 'allow_all'])
  assert.equal(MODE_LABELS.allow_all, 'allow all')
})
