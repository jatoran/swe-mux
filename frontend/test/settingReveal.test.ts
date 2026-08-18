import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import {
  SETTING_FLASH_CLASS,
  focusTarget,
  settingSelector,
  shouldFocusControl,
} from '../src/settingReveal.ts'

/** A control, duck-typed: these helpers read a tag, a type, and nothing else. */
const control = (tagName: string, type?: string) =>
  ({ tagName, type, querySelector: () => null }) as unknown as HTMLElement

test('a setting id becomes a selector that survives quotes and backslashes', () => {
  assert.equal(settingSelector('auto_delivery_enabled'), '[data-setting="auto_delivery_enabled"]')
  assert.equal(settingSelector('automation:code_graph'), '[data-setting="automation:code_graph"]')
  // Nothing in the catalogue needs escaping today; the point is that a future id cannot
  // break out of the attribute selector.
  assert.ok(settingSelector('odd"id').includes('\\"'))
})

test('focus goes to the control, whether the mark is on it or on its labelled row', () => {
  const checkbox = control('INPUT', 'checkbox')
  assert.equal(focusTarget(checkbox), checkbox)

  const row = {
    tagName: 'LABEL',
    querySelector: (selector: string) => selector === 'input,select,textarea,button' ? checkbox : null,
  } as unknown as HTMLElement
  assert.equal(focusTarget(row), checkbox)

  const prose = { tagName: 'P', querySelector: () => null } as unknown as HTMLElement
  assert.equal(focusTarget(prose), null)
})

test('touch devices are not handed a text field, because the keyboard would cover the answer', () => {
  const checkbox = control('INPUT', 'checkbox')
  const select = control('SELECT')
  const textField = control('INPUT', 'text')
  const area = control('TEXTAREA')

  for (const coarse of [false, true]) {
    assert.equal(shouldFocusControl(checkbox, coarse), true)
    assert.equal(shouldFocusControl(select, coarse), true)
    assert.equal(shouldFocusControl(null, coarse), false)
  }
  assert.equal(shouldFocusControl(textField, false), true)
  assert.equal(shouldFocusControl(textField, true), false)
  assert.equal(shouldFocusControl(area, false), true)
  assert.equal(shouldFocusControl(area, true), false)
})

test('the flash class is the one both arrival paths paint', () => {
  // Asserted as a value rather than a string literal at each call site: the search jump and
  // the deep-link reveal have to stay one visual language, and the stylesheet defines it once.
  assert.equal(SETTING_FLASH_CLASS, 'setting-flash')
  const style = readFileSync(join(import.meta.dirname, '..', 'src', 'style.css'), 'utf8')
  assert.ok(style.includes(`.${SETTING_FLASH_CLASS}{`), 'the flash class needs a rule')
  assert.ok(style.includes('@media(prefers-reduced-motion:reduce){.setting-flash{animation:none}}'))
})
