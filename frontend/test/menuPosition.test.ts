import assert from 'node:assert/strict'
import test from 'node:test'
import { clampContextMenuLeft, fitMenuInViewport, fitScrollingMenuInViewport } from '../src/menuPosition.ts'

type FakeMenu = {
  rect: { top: number; left: number; width: number; height: number }
  style: { top: string; left: string; maxHeight: string }
}

function fakeMenu(rect: FakeMenu['rect']): FakeMenu {
  return { rect, style: { top: `${rect.top}px`, left: `${rect.left}px`, maxHeight: '' } }
}

function withViewport(width: number, height: number, run: () => void): void {
  const globals = globalThis as { window?: unknown }
  const previous = globals.window
  globals.window = { innerWidth: width, innerHeight: height }
  try { run() } finally { globals.window = previous }
}

function asElement(menu: FakeMenu): HTMLElement {
  return { getBoundingClientRect: () => menu.rect, style: menu.style } as unknown as HTMLElement
}

test('context menus stay beside the pointer when the preferred width fits', () => {
  assert.equal(clampContextMenuLeft(120, 1000), 120)
})

test('context menus clamp to the viewport at the right edge', () => {
  assert.equal(clampContextMenuLeft(950, 1000), 696)
})

test('context menus fill very narrow viewports without escaping the gutter', () => {
  assert.equal(clampContextMenuLeft(150, 240), 4)
})

test('a menu opened near the bottom is lifted so its full height stays on-screen', () => {
  withViewport(390, 600, () => {
    // Long-press near the bottom seeded top=560; the real menu is 320px tall.
    const menu = fakeMenu({ top: 560, left: 40, width: 300, height: 320 })
    fitMenuInViewport(asElement(menu))
    assert.equal(menu.style.top, '276px') // 600 - 320 - 4
    assert.equal(menu.style.left, '40px') // already fits horizontally
  })
})

test('a menu that already fits within the viewport is left where it is', () => {
  withViewport(1200, 900, () => {
    const menu = fakeMenu({ top: 120, left: 200, width: 300, height: 400 })
    fitMenuInViewport(asElement(menu))
    assert.equal(menu.style.top, '120px')
    assert.equal(menu.style.left, '200px')
  })
})

test('a menu taller than the viewport is pinned to the top gutter', () => {
  withViewport(360, 400, () => {
    // CSS caps height at the viewport, but guard the degenerate case anyway.
    const menu = fakeMenu({ top: 300, left: 10, width: 300, height: 640 })
    fitMenuInViewport(asElement(menu))
    assert.equal(menu.style.top, '4px')
  })
})

test('a menu overflowing the right edge is nudged left', () => {
  withViewport(390, 800, () => {
    const menu = fakeMenu({ top: 100, left: 300, width: 300, height: 200 })
    fitMenuInViewport(asElement(menu))
    assert.equal(menu.style.left, '86px') // 390 - 300 - 4
  })
})

test('fitMenuInViewport ignores an unmounted (null) menu', () => {
  fitMenuInViewport(null)
})

test('a long Run menu is capped to the viewport and pinned to the top gutter', () => {
  withViewport(390, 700, () => {
    // Project with a pile of VS Code tasks, opened from a sidebar row halfway down.
    const menu = fakeMenu({ top: 380, left: 6, width: 378, height: 900 })
    fitScrollingMenuInViewport(asElement(menu))
    assert.equal(menu.style.maxHeight, '688px') // 700 - 6 * 2
    assert.equal(menu.style.top, '6px') // capped height fills the viewport
  })
})

test('a Run menu shorter than the viewport is lifted, not shrunk', () => {
  withViewport(390, 700, () => {
    const menu = fakeMenu({ top: 500, left: 6, width: 378, height: 400 })
    fitScrollingMenuInViewport(asElement(menu))
    assert.equal(menu.style.maxHeight, '') // no cap needed
    assert.equal(menu.style.top, '294px') // 700 - 400 - 6
  })
})

test('re-fitting an already capped menu does not ratchet it smaller', () => {
  withViewport(390, 700, () => {
    const menu = fakeMenu({ top: 380, left: 6, width: 378, height: 900 })
    fitScrollingMenuInViewport(asElement(menu))
    menu.rect.top = 6
    fitScrollingMenuInViewport(asElement(menu))
    assert.equal(menu.style.maxHeight, '688px')
    assert.equal(menu.style.top, '6px')
  })
})

test('fitScrollingMenuInViewport ignores an unmounted (null) menu', () => {
  fitScrollingMenuInViewport(null)
})
