import assert from 'node:assert/strict'
import test from 'node:test'
import { clampContextMenuLeft } from '../src/menuPosition.ts'

test('context menus stay beside the pointer when the preferred width fits', () => {
  assert.equal(clampContextMenuLeft(120, 1000), 120)
})

test('context menus clamp to the viewport at the right edge', () => {
  assert.equal(clampContextMenuLeft(950, 1000), 696)
})

test('context menus fill very narrow viewports without escaping the gutter', () => {
  assert.equal(clampContextMenuLeft(150, 240), 4)
})
