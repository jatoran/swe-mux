import { devices, expect, test } from 'playwright/test'
import { harnessReady } from './harnessReady'

// Kept in step with `Scenario` in jumpLatest.ts by hand: that module is browser-only
// (it imports CSS and touches the DOM at load), so Node cannot import a list from it.
const SCENARIOS = [
  'plain', 'full-scrollback', 'mouse-tracking', 'alt-screen-round-trip', 'wrapped-narrow', 'repaint',
] as const

// The chip only exists for touch, so measure it as a phone: real touch events, real device
// metrics. `page.touchscreen.tap` goes through hit-testing, so this also covers the chip
// being reachable at all — it overlays `.terminal-host`, whose `.xterm` carries
// `touch-action:none` under the mobile media query.
test.use({ ...devices['Pixel 7'] })

for (const scenario of SCENARIOS) {
  test(`tapping the jump-to-latest chip reaches the tail (${scenario})`, async ({ page }) => {
    await page.goto('/jump-latest-harness.html')
    await harnessReady(page, 'setupJumpLatest')
    const before = await page.evaluate(name => window.setupJumpLatest(name), scenario)
    expect(before.onTail, 'setup should leave the viewport off the tail').toBe(false)

    const box = (await page.locator('#chip').boundingBox())!
    await page.touchscreen.tap(box.x + box.width / 2, box.y + box.height / 2)
    await page.waitForTimeout(200)

    const after = await page.evaluate(() => window.readJumpLatest())
    expect(after.clicks, 'the tap should reach the chip').toBe(1)
    expect(after.onTail, `chip left the viewport at ${after.viewportY}/${after.baseY}`).toBe(true)
  })
}

// The command rail's keys reach the tail through xterm's `scrollOnUserInput` rather than
// through `scrollToBottom()`, so they are a different code path. Any divergence between the
// two is the shape of this bug, and it is the pair — not the chip alone — that has to hold.
test('the chip and the command rail agree in every scroll state', async ({ page }) => {
  await page.goto('/jump-latest-harness.html')
  await harnessReady(page, 'runJumpLatestScenarios')
  const cases = await page.evaluate(() => window.runJumpLatestScenarios())
  const summary = cases.map(item =>
    `${item.name}: ${item.before.viewportY}/${item.before.baseY} -> ${item.after.viewportY}/${item.after.baseY}` +
    ` ${item.after.onTail ? 'on-tail' : 'OFF-TAIL'}`)
  // `unfixed-*` reproduces the defect with a bare `scrollToBottom()`. If it ever stops
  // landing short, xterm has fixed the clamp upstream and `scrollTerminalToTail` can go.
  const offTail = cases.filter(item => !item.after.onTail).map(item => item.name)
  expect(offTail, summary.join('\n')).toEqual(['unfixed-keyboard-then-chip'])
})
