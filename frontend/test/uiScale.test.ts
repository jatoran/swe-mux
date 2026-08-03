import assert from 'node:assert/strict'
import test from 'node:test'
import {
  DEFAULT_UI_SCALE,
  UI_SCALE_BASE_PX,
  UI_SCALE_STEPS,
  scaledFontSize,
  uiScaleFrom,
  uiScaleLabel,
} from '../src/uiScale.ts'

test('each device class reads its own key', () => {
  const config = { ui_scale_desktop: 1.0, ui_scale_mobile: 1.25 }
  assert.equal(uiScaleFrom(config, 'desktop'), 1.0)
  assert.equal(uiScaleFrom(config, 'mobile'), 1.25)
})

test('every published step round-trips', () => {
  for (const step of UI_SCALE_STEPS) {
    assert.equal(uiScaleFrom({ ui_scale_desktop: step }, 'desktop'), step)
  }
})

// A daemon older than this build sends neither key, and the browser must render
// at today's size rather than at nothing.
test('a config without the keys is the default scale', () => {
  assert.equal(uiScaleFrom({}, 'desktop'), DEFAULT_UI_SCALE)
  assert.equal(uiScaleFrom({}, 'mobile'), DEFAULT_UI_SCALE)
})

test('anything not a finite number is the default scale', () => {
  for (const raw of ['1.25', null, undefined, {}, [], NaN, Infinity, true]) {
    assert.equal(uiScaleFrom({ ui_scale_desktop: raw }, 'desktop'), DEFAULT_UI_SCALE)
  }
})

// The daemon validates against the same list, so an off-list value means a
// hand-edited config.toml. Falling back beats rendering at 4x.
test('an off-list number falls back rather than being honoured', () => {
  assert.equal(uiScaleFrom({ ui_scale_desktop: 3 }, 'desktop'), DEFAULT_UI_SCALE)
  assert.equal(uiScaleFrom({ ui_scale_desktop: 0.2 }, 'desktop'), DEFAULT_UI_SCALE)
  assert.equal(uiScaleFrom({ ui_scale_desktop: 1.05 }, 'desktop'), DEFAULT_UI_SCALE)
})

// TOML → JSON → JS does not preserve exact float bits, so membership is tested
// with a tolerance and a value a hair off a step must still resolve to it.
test('a float a hair off a step snaps to it', () => {
  assert.equal(uiScaleFrom({ ui_scale_desktop: 1.25 + 1e-12 }, 'desktop'), 1.25)
  assert.equal(uiScaleFrom({ ui_scale_desktop: 0.9 - 1e-12 }, 'desktop'), 0.9)
})

test('labels carry both the percentage and the resulting px', () => {
  assert.equal(uiScaleLabel(1.0), '100%  (11px)')
  assert.equal(uiScaleLabel(1.25), '125%  (13.8px)')
  assert.equal(uiScaleLabel(0.9), '90%  (9.9px)')
})

test('1 is the default, so an untouched install renders at the historical size', () => {
  assert.equal(DEFAULT_UI_SCALE, 1.0)
  assert.equal(UI_SCALE_BASE_PX, 11)
  assert.ok(UI_SCALE_STEPS.includes(DEFAULT_UI_SCALE))
})

test('a scaled font size matches what the stylesheet computes for chrome', () => {
  // `--ui-font-size` is a raw `calc(11px * scale)` with no rounding, so the terminal has
  // to land on the same value or the two surfaces drift apart at the same setting.
  assert.deepEqual(
    UI_SCALE_STEPS.map(step => scaledFontSize(UI_SCALE_BASE_PX, step)),
    [9.9, 11, 12.1, 13.75, 15.4],
  )
})

test('scaled sizes keep enough precision to tell adjacent steps apart', () => {
  // Rounded to whole pixels, 1.1 and 1.25 would both render 11px→12px and 13.75px→14px
  // reads as a step the chrome beside it did not take. Two decimals is what xterm needs
  // to actually move.
  assert.notEqual(scaledFontSize(11, 1.1), scaledFontSize(11, 1.25))
  assert.equal(scaledFontSize(11, 1.25), 13.75)
  // Binary float noise does not leak into the value handed to xterm.
  assert.equal(scaledFontSize(11, 1.1), 12.1)
})
