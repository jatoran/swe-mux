import type { Page } from 'playwright/test'

// Touch plumbing shared by the Action rail's renderer specs.
//
// Real touches through CDP, not synthetic `PointerEvent`s, and that is the whole reason
// these specs exist rather than unit tests: `OverflowRail` calls `setPointerCapture` on
// its wrapper as soon as a touch lands on an overflowing strip, synthetic events cannot be
// captured, and Chrome's own touch-to-click synthesis is the third party to every one of
// these arguments. Only trusted input reproduces the arrangement under test.

export type Finger = {
  down: (x: number, y: number) => Promise<unknown>
  move: (x: number, y: number) => Promise<unknown>
  up: () => Promise<unknown>
}

export async function touch(page: Page): Promise<Finger> {
  const cdp = await page.context().newCDPSession(page)
  type TouchType = 'touchStart' | 'touchMove' | 'touchEnd'
  const send = (type: TouchType, points: Array<{ x: number; y: number }>) =>
    cdp.send('Input.dispatchTouchEvent', { type, touchPoints: points.map(point => ({ x: point.x, y: point.y })) })
  return {
    down: (x: number, y: number) => send('touchStart', [{ x, y }]),
    move: (x: number, y: number) => send('touchMove', [{ x, y }]),
    up: () => send('touchEnd', []),
  }
}

/**
 * The centre of one rail key, clamped into the part of the strip a finger can actually
 * reach. The strip is scrolled and clipped, and the overflow controls are drawn *over* its
 * ends, so a key's layout box can sit somewhere a touch aimed at its centre would land on
 * an edge button instead.
 */
export async function keyPoint(page: Page, selector: string): Promise<{ x: number; y: number }> {
  const point = await page.evaluate((keySelector: string) => {
    const strip = document.querySelector<HTMLElement>('.terminal-action-scroll')!
    const key = strip.querySelector<HTMLElement>(keySelector)
    if (!key) return null
    const stripBox = strip.getBoundingClientRect()
    const keyBox = key.getBoundingClientRect()
    let left = Math.max(keyBox.left, stripBox.left)
    let right = Math.min(keyBox.right, stripBox.right)
    const before = document.querySelector<HTMLElement>('.overflow-rail-left')
    const after = document.querySelector<HTMLElement>('.overflow-rail-right')
    if (before) left = Math.max(left, before.getBoundingClientRect().right)
    if (after) right = Math.min(right, after.getBoundingClientRect().left)
    return right - left < 8 ? null : { x: (left + right) / 2, y: keyBox.top + keyBox.height / 2 }
  }, selector)
  if (!point) throw new Error(`rail key ${selector} is not reachable inside the strip`)
  return point
}

export const sends = (page: Page): Promise<string[]> => page.evaluate(() => window.railSends)
export const claims = (page: Page): Promise<boolean[]> => page.evaluate(() => window.railClaims)
export const scrollLeft = (page: Page): Promise<number> =>
  page.evaluate(() => document.querySelector('.terminal-action-scroll')!.scrollLeft)

/** Drag from a point in steps, so the recogniser sees travel rather than a teleport. */
export async function dragBy(
  finger: Finger,
  from: { x: number; y: number },
  dx: number,
  dy: number,
  steps = 6,
): Promise<void> {
  for (let step = 1; step <= steps; step += 1) {
    await finger.move(from.x + (dx * step) / steps, from.y + (dy * step) / steps)
  }
}

/** Reset the harness's recorders without reloading it. */
export const clearRecorders = (page: Page): Promise<void> =>
  page.evaluate(() => { window.railSends.length = 0; window.railClaims.length = 0 })
