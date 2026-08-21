import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const root = join(import.meta.dirname, '..')
const source = (name: string) => readFileSync(join(root, 'src', name), 'utf8')

// Every install-wide spending cap has to reach the same control, or the promise the
// section makes - "tokens, dollars, or first hit, everywhere" - is true of whichever
// budgets someone remembered to convert. A cap that shipped with a bespoke number box
// would look identical to the operator until they went looking for the choice.
const BUDGETS = [
  'automation_daily_budget',
  'automation_rule_daily_budget',
  'project_card_daily_budget',
  'scan_timeline_daily_budget',
  'scan_timeline_run_budget',
  'attention_narration_daily_budget',
  'tts_daily_budget',
  'assistant_daily_budget',
]

test('every spending cap is edited through the one shared control', () => {
  const settings = source('Settings.tsx')
  for (const name of BUDGETS) {
    assert.ok(
      settings.includes(`<BudgetControl name="${name}"`),
      `${name} does not use BudgetControl, so it cannot offer the tokens/dollars choice`,
    )
  }
})

test('no cap is still edited as a bare number box', () => {
  const settings = source('Settings.tsx')
  // The pre-`Budget` field names. Any of them reappearing means a second, older
  // editor for a cap that already has one - the exact split the shape removed.
  for (const legacy of [
    'automation_daily_token_budget', 'automation_daily_budget_usd',
    'automation_rule_daily_token_budget', 'automation_rule_daily_budget_usd',
    'scan_timeline_daily_token_budget', 'scan_timeline_daily_budget_usd',
    'scan_timeline_run_token_budget', 'assistant_daily_budget_usd',
    'tts_daily_budget_usd', 'project_card_daily_budget_usd',
    'attention_narration_daily_budget_usd',
  ]) {
    assert.ok(!settings.includes(legacy), `Settings still edits the retired ${legacy}`)
  }
})

test('the control states what a dollar cap does when cost is unmeasurable', () => {
  const control = source('BudgetControl.tsx')
  // The whole point of the warning: an operator choosing "Dollars" against a local
  // endpoint is choosing a cap that cannot bind, and has to be told at that moment
  // rather than discovering it as a runaway bill-free spend months later.
  assert.ok(control.includes('COST_BLIND_WARNING'))
  assert.ok(control.includes('cannot bind here'))
  assert.ok(control.includes('unreported cost is unknown, never zero'))
  // And it is drawn only while the dollar axis is actually enforced.
  assert.ok(control.includes('usdOn && reportsCost === false'))
  // Absent knowledge must not render as an accusation, so the check is strict `false`
  // rather than falsy - `undefined` means "not asked yet".
  assert.ok(!control.includes('!reportsCost'))
})

test('an unenforced axis is kept and marked rather than cleared or disabled', () => {
  const control = source('BudgetControl.tsx')
  assert.ok(control.includes('Not enforced in this mode. Kept so switching back does not lose it.'))
  assert.ok(!control.includes('disabled='), 'an unenforced figure is still the operator’s to edit')
})

test('switching into a mode seeds the axis it starts enforcing', () => {
  // A mode change that left the newly-enforced axis empty would save a budget the
  // daemon rejects, naming a field the operator never saw.
  const control = source('BudgetControl.tsx')
  assert.ok(control.includes("enforces(next, 'tokens') && value.tokens == null ? maxTokens"))
  assert.ok(control.includes("enforces(next, 'usd') && value.usd == null ? maxUsd"))
})
