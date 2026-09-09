import assert from 'node:assert/strict'
import test from 'node:test'
import { parseDevicePreference, resolveDeviceMode } from '../src/deviceMode.ts'

test('touch-primary phones and tablets retain their mobile profile and layout at every width', () => {
  for (const narrow of [true, false]) {
    const mode = resolveDeviceMode({ narrow, coarse: true, hover: false })
    assert.equal(mode.profile, 'mobile')
    assert.equal(mode.layout, 'mobile')
    assert.equal(mode.reason, 'touch-primary')
  }
})

test('narrow desktop windows compact the layout without switching device preferences', () => {
  const mode = resolveDeviceMode({ narrow: true, coarse: false, hover: true })
  assert.equal(mode.profile, 'desktop')
  assert.equal(mode.layout, 'mobile')
  assert.equal(resolveDeviceMode({ narrow: false, coarse: false, hover: true }).layout, 'desktop')
})

test('a laptop or stylus that can hover does not become mobile merely from coarse input', () => {
  assert.equal(resolveDeviceMode({ narrow: false, coarse: true, hover: true }).profile, 'desktop')
  assert.equal(resolveDeviceMode({ narrow: false, coarse: false, hover: false }).profile, 'desktop')
})

test('explicit overrides select both profile and layout without falsifying input capabilities', () => {
  const desktop = resolveDeviceMode({ narrow: true, coarse: true, hover: false }, 'desktop')
  assert.equal(desktop.layout, 'desktop')
  assert.equal(desktop.profile, 'desktop')
  assert.equal(desktop.coarse, true)
  const mobile = resolveDeviceMode({ narrow: false, coarse: false, hover: true }, 'mobile')
  assert.equal(mobile.layout, 'mobile')
  assert.equal(mobile.profile, 'mobile')
  assert.equal(mobile.hover, true)
})

test('invalid stored device preferences safely resolve to auto', () => {
  for (const invalid of [null, undefined, '', 'tablet', '{}', 'MOBILE', 1]) {
    assert.equal(parseDevicePreference(invalid), 'auto')
  }
  assert.equal(parseDevicePreference('desktop'), 'desktop')
  assert.equal(parseDevicePreference('mobile'), 'mobile')
})
