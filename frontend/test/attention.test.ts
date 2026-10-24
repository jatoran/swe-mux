import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { BUDGET_NOTE, CHANNEL_ORDER, budgetLine, fanoutHeadline, suppressedLabel, type AttentionBudget, type FanOut } from '../src/attention.ts'

const source = (name:string) => readFileSync(join(import.meta.dirname, '..', 'src', name), 'utf8')

test('fan-out reports itself unknown rather than inventing a number', () => {
  const scarce:FanOut = {status:'insufficient_samples',samples:2,required:5,interaction_seconds:null,neglect_seconds:null,sustainable_agents:null,attended_now:6}
  assert.match(fanoutHeadline(scarce),/unknown: 2 of 5/)
  const measured:FanOut = {status:'ok',samples:12,required:5,interaction_seconds:120,neglect_seconds:900,sustainable_agents:8,attended_now:6}
  assert.match(fanoutHeadline(measured),/sustainable at about 8 attended agents/)
})

test('the budget line shows the daily bound and the burst limiter under it', () => {
  const budget:AttentionBudget = {day:'2026-08-13',daily_budget:4,used:3,remaining:1,hourly_cap:2,burst_used:1,burst_remaining:1}
  assert.equal(budgetLine(budget),'3/4 interrupts today · 1/2 this hour')
})

test('the budget line says in visible text that a waiting permission prompt never spends it', () => {
  assert.equal(BUDGET_NOTE,'Permission prompts and anything else quick to resolve never count as interrupts, however many are waiting, and a session waiting on you shows as awaiting even with the budget spent.')
  assert.ok(!BUDGET_NOTE.includes('—'))
  // Drawn as text right under the counter, inside every inbox instance (both Alerts
  // tabs and the dashboard's Activity tab), never as a hover title: a phone cannot hover.
  const inbox = source('AttentionInbox.tsx')
  const counter = inbox.indexOf('<p class="attention-budget">{budgetLine(data.budget)}')
  const note = inbox.indexOf('<span class="attention-budget-note">{BUDGET_NOTE}</span>')
  assert.ok(counter >= 0 && note > counter)
  assert.ok(inbox.indexOf('</p>', counter) > note)
  assert.ok(!/title=\{BUDGET_NOTE\}/.test(inbox))
})

test('every suppression reason is spelled out, including a user rule', () => {
  assert.match(suppressedLabel('budget_exhausted'),/budget is spent/)
  assert.match(suppressedLabel('superseded_run'),/conversation was replaced/)
  assert.match(suppressedLabel('rule:stuck'),/your rule for stuck/)
  assert.match(suppressedLabel('something_new'),/held back: something_new/)
})

test('interrupt-now leads and the digest trails, so cheap and expensive work never merge', () => {
  assert.deepEqual(CHANNEL_ORDER,['interrupt_now','next_breakpoint','inbox','digest'])
})

test('the inbox draws channels as groups and always shows what it held back', () => {
  const inbox = source('AttentionInbox.tsx')
  assert.ok(inbox.includes('(visibleChannels||CHANNEL_ORDER).filter'))
  assert.ok(inbox.includes('class="attention-channel-hint"'))
  assert.ok(inbox.includes('attention-suppressed'))
  assert.ok(inbox.includes('nothing held back'))
  // Rationale is evidence-first: the deterministic summary is the row, the
  // model's "why" is an aside drawn under it.
  assert.ok(inbox.indexOf('{item.summary}') < inbox.indexOf('{item.narration}'))
})

test('a mined rule is offered for acceptance and never applied silently', () => {
  const inbox = source('AttentionInbox.tsx')
  assert.ok(inbox.includes("rule.state==='proposed'"))
  assert.ok(inbox.includes('decideAttentionRule(rule.incident_class,rule.channel,true)'))
  assert.ok(inbox.includes('decideAttentionRule(rule.incident_class,rule.channel,false)'))
  assert.ok(inbox.includes('drop rule'))
})

test('ranked attention leads the Alerts tab and reaches no device', () => {
  const notifications = source('Notifications.tsx')
  const attention = source('attention.ts')
  const inbox = source('AttentionInbox.tsx')
  // The inbox is fleet-wide; the Project rides along only so an empty inbox can say
  // whether that Project permitted ranking at all.
  assert.ok(notifications.includes("visibleChannels={['interrupt_now']}"))
  assert.ok(notifications.includes("visibleChannels={['next_breakpoint','inbox','digest']}"))
  assert.ok(notifications.includes('>Review next</button>'))
  assert.ok(notifications.indexOf('AttentionInbox onOpenSession') < notifications.indexOf('notification-list'))
  // The daemon states the boundary rather than leaving it implied, and nothing
  // on this surface subscribes a device or posts to the push routes.
  assert.ok(attention.includes('delivery:{push:boolean;surface:string}'))
  assert.ok(!inbox.includes('pushManager') && !inbox.includes('/api/push'))
})
