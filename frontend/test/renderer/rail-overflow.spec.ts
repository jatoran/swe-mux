import { expect, test, type Page } from 'playwright/test'

/**
 * The pinned rail and its overflow popover, against real geometry and real compositing.
 *
 * Every property here is decided outside any one module and is invisible to a unit test:
 * the split is a measurement of live chips, the `+N` chip's width is a CSS variable, the
 * popover's glass is the browser's own `backdrop-filter` over whatever the terminal is
 * showing, and "a drop-up opened from inside the panel does not dismiss it" is a race
 * between two independent `window` listeners.
 *
 * The failures this exists to catch are all quiet ones: a `+N` chip on a rail that visibly
 * has room, a chip silently lost between the row and the panel, a panel that closes on the
 * first of a two-click confirm, and a glass that reads fine over a dark buffer and turns to
 * grey-on-grey the moment someone opens a white diff.
 */

const MORE = '.rail-more'
const PANEL = '.rail-overflow-popover'
const GRID = '.rail-overflow-grid'
// Scoped through `.overflow-rail`, which the hidden measuring twin is deliberately outside
// of: it carries the same `.terminal-action-scroll` class (that is how its chips measure at
// the width they would really render at), so an unscoped selector counts every chip twice.
const STRIP = '.overflow-rail .terminal-action-scroll'
const ROW_CHIPS = `${STRIP} > [data-key]`
const DROPUP = '.rail-dropup'

test.use({ viewport: { width: 520, height: 420 } })

test.beforeEach(async ({ page }) => {
  await page.goto('/rail-overflow-harness.html')
  await page.waitForSelector(MORE)
})

/** Open the panel and wait until it is *placed*, not merely present — the effect that
 *  positions it is the same one that installs its dismissal listeners. */
async function open(page: Page) {
  await page.click(MORE)
  await expect(page.locator(PANEL)).toBeVisible()
  await expect.poll(async () => page.locator(PANEL).evaluate(el => (el as HTMLElement).style.left)).not.toBe('')
}

/** Every chip in the harness, however it is currently split. */
async function total(page: Page): Promise<number> {
  return page.locator('.rail-row-measure > *').count()
}

/** Open a drop-up from a chip inside the panel, and wait for it to be placed. Placement and
 *  the drop-up's own Escape listener are installed by the same effect, so a spec that acted
 *  on `toBeVisible` alone would press Escape into a panel not yet listening for it. */
async function openDropup(page: Page) {
  await page.click(`${GRID} [data-key="clip"]`)
  await expect(page.locator(DROPUP)).toBeVisible()
  await expect.poll(async () => page.locator(DROPUP).evaluate(el => (el as HTMLElement).style.left)).not.toBe('')
}

test('the row keeps what fits and the rest is exactly the `+N` count, losing nothing', async ({ page }) => {
  const pinned = await page.locator(ROW_CHIPS).count()
  const label = await page.locator(MORE).textContent()
  const hidden = Number((label || '').replace('+', ''))
  expect(hidden).toBeGreaterThan(0)
  // No chip may be in both places and none may be in neither: the panel is the rest of
  // the row, not a second menu that happens to look like it.
  expect(pinned + hidden).toBe(await total(page))

  await open(page)
  expect(await page.locator(`${GRID} > *`).count()).toBe(hidden)
})

test('the pinned chips and the overflow chip fit the row they were measured against', async ({ page }) => {
  const strip = page.locator(STRIP)
  const [box, more] = await Promise.all([strip.boundingBox(), page.locator(MORE).boundingBox()])
  expect(box).not.toBeNull()
  expect(more).not.toBeNull()
  // The one control that exists to absorb overflow must never be the thing that overflows.
  expect(more!.x + more!.width).toBeLessThanOrEqual(box!.x + box!.width + 1)
  // And the row genuinely does not scroll, which is what the split replaced.
  const scrolls = await strip.evaluate(el => el.scrollWidth - el.clientWidth)
  expect(scrolls).toBeLessThanOrEqual(1)
})

test('the overflow chip sits on the trailing edge, not wherever the split stopped', async ({ page }) => {
  // Every edge read inside one evaluate. Separate `boundingBox()` calls resolve at
  // different moments, and a viewport change between two of them reports one box from
  // before the relayout and one from after - which reads as a real geometry failure.
  const measure = () => page.evaluate(() => {
    const strip = document.querySelector<HTMLElement>('.overflow-rail .terminal-action-scroll')!
    const right = (selector: string) => strip.querySelector(selector)!.getBoundingClientRect().right
    const chips = Array.from(strip.querySelectorAll<HTMLElement>(':scope > [data-key]'))
    return {
      moreLeft: strip.querySelector('.rail-more')!.getBoundingClientRect().left,
      moreRight: right('.rail-more'),
      lastChipRight: chips[chips.length - 1].getBoundingClientRect().right,
      stripRight: strip.getBoundingClientRect().right,
      padRight: Number.parseFloat(getComputedStyle(strip).paddingRight) || 0,
    }
  })
  for (const width of [420, 520, 760]) {
    await page.setViewportSize({ width, height: 420 })
    await expect(page.locator(MORE)).toBeVisible()
    // Polled, because a viewport change is not settled by the time the chip is visible and
    // a single read can catch the row mid-relayout.
    // The `+N` chip is the trailing cluster's last element (the strip carries no gear),
    // so its right edge IS the rail's content edge.
    await expect.poll(async () => {
      const settling = await measure()
      return Math.round(settling.stripRight - settling.padRight - settling.moreRight)
    }, { message: `the cluster is off the trailing edge at ${width}px` }).toBe(0)
    const box = await measure()
    // The slack the split leaves is real: the chip is measurably clear of the last pinned
    // chip, which is exactly the gap that used to move it from rail to rail.
    expect(box.moreLeft).toBeGreaterThanOrEqual(box.lastChipRight)
  }
})

test('a row with room for everything shows no `+N` chip at all', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 420 })
  await expect(page.locator(MORE)).toHaveCount(0)
  expect(await page.locator(ROW_CHIPS).count()).toBe(await total(page))
  // The strip is then indistinguishable from the pre-split rail.
  const scrolls = await page.locator(STRIP).evaluate(el => el.scrollWidth - el.clientWidth)
  expect(scrolls).toBeLessThanOrEqual(1)
})

test('the `+N` count follows the width live', async ({ page }) => {
  // Read in one evaluate rather than as count-then-read: the chip disappears the moment
  // the row grows enough, and a `textContent()` issued against a locator that resolved a
  // frame earlier auto-waits for an element that is never coming back.
  const count = async () => page.evaluate(() => {
    const chip = document.querySelector('.rail-more')
    return chip ? Number((chip.textContent || '').replace('+', '')) : 0
  })
  const narrow = await count()
  await page.setViewportSize({ width: 900, height: 420 })
  await expect.poll(count).toBeLessThan(narrow)
  await page.setViewportSize({ width: 420, height: 420 })
  await expect.poll(count).toBeGreaterThan(narrow)
})

test('the panel opens upward and lands on the rail\'s trailing edge, inside the viewport', async ({ page }) => {
  await open(page)
  const [box, more, viewport] = await Promise.all([
    page.locator(PANEL).boundingBox(),
    page.locator(MORE).boundingBox(),
    page.evaluate(() => ({ width: window.innerWidth, height: window.innerHeight })),
  ])
  // The rail's trailing edge, which is the `+N` chip's right edge now that the strip
  // carries no gear.
  expect(Math.abs((box!.x + box!.width) - (more!.x + more!.width))).toBeLessThanOrEqual(1)
  expect(box!.y + box!.height).toBeLessThanOrEqual(more!.y + 1)
  expect(box!.y).toBeGreaterThanOrEqual(0)
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1)
  // Never a blanket over the composer above it.
  expect(box!.height).toBeLessThanOrEqual(viewport.height * 0.5 + 30)
})

test('using the panel does not close it, so a two-click confirm completes in place', async ({ page }) => {
  await open(page)
  const confirm = page.locator(`${GRID} [data-key="endSession"]`)
  await confirm.click()
  await expect(page.locator(PANEL)).toBeVisible()
  await expect(confirm).toHaveText('Confirm ✓')
  await confirm.click()
  await expect(page.locator(PANEL)).toBeVisible()
  expect(await page.evaluate(() => window.railOverflowFires)).toEqual(['endSession:arm', 'endSession:confirm'])
})

test('the panel dismisses on Escape, on an outside press, and on its own close control', async ({ page }) => {
  await open(page)
  await page.keyboard.press('Escape')
  await expect(page.locator(PANEL)).toHaveCount(0)

  await open(page)
  await page.locator('#buffer').click({ position: { x: 20, y: 20 } })
  await expect(page.locator(PANEL)).toHaveCount(0)

  await open(page)
  await page.click('.rail-overflow-close')
  await expect(page.locator(PANEL)).toHaveCount(0)
})

test('a drop-up opened from inside the panel renders over it and leaves it standing', async ({ page }) => {
  await open(page)
  await openDropup(page)
  // The tap that opened the drop-up is an outside-press for the panel's own dismissal,
  // which is exactly the case that must be exempted.
  await expect(page.locator(PANEL)).toBeVisible()

  // Over it, not merely present: the two overlap, and whatever is on top owns the pixel.
  const overlap = await page.evaluate(() => {
    const dropup = document.querySelector('.rail-dropup')!.getBoundingClientRect()
    const panel = document.querySelector('.rail-overflow-popover')!.getBoundingClientRect()
    const x = Math.max(dropup.left, panel.left) + 3
    const y = Math.max(dropup.top, panel.top) + 3
    if (x > Math.min(dropup.right, panel.right) || y > Math.min(dropup.bottom, panel.bottom)) return 'no-overlap'
    return document.elementFromPoint(x, y)?.closest('.rail-dropup, .rail-overflow-popover')?.className ?? 'none'
  })
  expect(overlap).toContain('rail-dropup')

  // Picking from the drop-up inserts and closes the drop-up; the rail behind it stays.
  await page.click('[data-row="row-1"]')
  await expect(page.locator(DROPUP)).toHaveCount(0)
  await expect(page.locator(PANEL)).toBeVisible()
  expect(await page.evaluate(() => window.railOverflowFires)).toEqual(['clip:1'])

  // Escape belongs to the drop-up while one is open, and to the panel afterwards.
  await openDropup(page)
  await page.keyboard.press('Escape')
  await expect(page.locator(DROPUP)).toHaveCount(0)
  await expect(page.locator(PANEL)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator(PANEL)).toHaveCount(0)
})

test('the panel offers the rail editor from its header and closes as it hands over', async ({ page }) => {
  await open(page)
  // Chrome beside the close control, not a chip in the grid: pressing it configures the
  // rail rather than writing something into the terminal.
  await expect(page.locator(`${GRID} .rail-overflow-configure`)).toHaveCount(0)
  await page.click('.rail-overflow-configure')
  expect(await page.evaluate(() => window.railOverflowFires)).toEqual(['configure'])
  // The editor replaces the whole rail area, so a panel left standing would float over a
  // surface that no longer exists.
  await expect(page.locator(PANEL)).toHaveCount(0)
})

test('a selection that opens a drawer or the library collapses the panel', async ({ page }) => {
  await open(page)
  await page.click(`${GRID} [data-key="drawer"]`)
  await expect(page.locator(PANEL)).toHaveCount(0)

  // Including one arriving from a drop-up's sticky exit, which is a departure by the same
  // route and has to fold the same panel.
  await open(page)
  await openDropup(page)
  await page.click('.rail-dropup-open')
  await expect(page.locator(PANEL)).toHaveCount(0)
})

/**
 * The composited background of a chip label inside the panel, as pixels.
 *
 * Sampled from a patch inside the chip that is above its text — the chip is 27px tall and
 * its label is vertically centred, so the rows just under the border are background. The
 * point of measuring rather than computing is `backdrop-filter`: the blur is the browser's,
 * and its contribution to the composite is not something a formula in a test can assert.
 */
async function labelBackground(page: Page, selector: string, dy: number): Promise<[number, number, number]> {
  const box = (await page.locator(selector).first().boundingBox())!
  const shot = await page.screenshot({ clip: { x: box.x + 3, y: box.y + dy, width: 6, height: 3 } })
  return page.evaluate(async png => {
    const bitmap = await createImageBitmap(await (await fetch(`data:image/png;base64,${png}`)).blob())
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height)
    const context = canvas.getContext('2d')!
    context.drawImage(bitmap, 0, 0)
    const data = context.getImageData(0, 0, bitmap.width, bitmap.height).data
    const totals = [0, 0, 0]
    for (let index = 0; index < data.length; index += 4) {
      totals[0] += data[index]; totals[1] += data[index + 1]; totals[2] += data[index + 2]
    }
    const pixels = data.length / 4
    return totals.map(value => value / pixels) as [number, number, number]
  }, shot.toString('base64'))
}

function contrast(a: readonly number[], b: readonly number[]): number {
  const channel = (value: number) => {
    const scaled = value / 255
    return scaled <= 0.04045 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4
  }
  const luminance = (colour: readonly number[]) =>
    0.2126 * channel(colour[0]) + 0.7152 * channel(colour[1]) + 0.0722 * channel(colour[2])
  const first = luminance(a) + 0.05
  const second = luminance(b) + 0.05
  return first > second ? first / second : second / first
}

test('every rail overlay holds 4.5:1 through the glass over a white and a black terminal', async ({ page }) => {
  await open(page)
  const text = await page.locator(`${GRID} > button`).first()
    .evaluate(el => getComputedStyle(el).color.match(/[\d.]+/g)!.slice(0, 3).map(Number))

  // Both compositions, worst first. A drop-up row is transparent over its panel, so its
  // label sits on ONE layer of glass; a popover chip has its own background and sits on two.
  // Sampled with the other overlay closed, since they deliberately overlap.
  const readings: Record<string, [number, number, number][]> = { 'popover chip': [], 'drop-up row': [] }
  for (const buffer of ['#ffffff', '#000000']) {
    await page.evaluate(colour => window.setBuffer(colour), buffer)
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
    readings['popover chip'].push(await labelBackground(page, `${GRID} > button`, 3))
    await openDropup(page)
    readings['drop-up row'].push(await labelBackground(page, '.rail-dropup-row', 4))
    await page.keyboard.press('Escape')
    await expect(page.locator(DROPUP)).toHaveCount(0)
  }

  for (const [surface, [bright, dark]] of Object.entries(readings)) {
    // The blur is doing its job only if the two differ: identical readings would mean the
    // panel is opaque and the glass is decorative.
    expect(
      Math.abs(bright[0] - dark[0]) + Math.abs(bright[1] - dark[1]) + Math.abs(bright[2] - dark[2]),
      `${surface} reads the same over white and black, so it is not translucent`,
    ).toBeGreaterThan(3)
    expect(contrast(text, bright), `${surface} over a white buffer: ${contrast(text, bright).toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5)
    expect(contrast(text, dark), `${surface} over a black buffer: ${contrast(text, dark).toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5)
  }
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 760 } })

  // Each overlay with the element it hangs off: the popover off the rail's trailing cluster,
  // and a drop-up off whichever chip opened it - here one inside the popover, which is also
  // the nesting the keyboard has to keep working.
  const overlays: [string, string, string, (page: Page) => Promise<void>][] = [
    ['the overflow popover', PANEL, '.rail-row-trailing', open],
    ['a drop-up', DROPUP, `${GRID} [data-key="clip"]`, async page => { await open(page); await openDropup(page) }],
  ]

  for (const [name, selector, trigger, show] of overlays) {
    test(`${name} takes half the screen, right-aligned to the rail`, async ({ page }) => {
      await show(page)
      const [box, strip, viewport] = await Promise.all([
        page.locator(selector).boundingBox(),
        page.locator(STRIP).boundingBox(),
        page.evaluate(() => ({ width: window.innerWidth })),
      ])
      // Half the screen at most: the terminal it is opened over has to stay readable.
      expect(box!.width).toBeLessThanOrEqual(viewport.width / 2 + 1)
      expect(box!.width).toBeGreaterThan(viewport.width / 3)
      // Right-aligned, so both overlays land in the same place rather than each hanging
      // off wherever its own trigger happens to sit.
      expect(box!.x + box!.width).toBeGreaterThanOrEqual(strip!.x + strip!.width - 20)
      expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width)
    })

    test(`${name} stays just above its trigger when the soft keyboard is up`, async ({ page }) => {
      // Both edges in one read, and measured against the *trigger* rather than the rail
      // element: the rail has padding above its chips, so an overlay correctly seated a few
      // pixels above one is legitimately inside the rail's own box.
      const seat = ([overlay, anchor]: [string, string]) => page.evaluate(([sel, ref]) => {
        const panel = document.querySelector(sel)!.getBoundingClientRect()
        const trigger = document.querySelector(ref)!.getBoundingClientRect()
        return { gap: Math.round(trigger.top - panel.bottom), top: Math.round(panel.top), bottom: Math.round(panel.bottom) }
      }, [overlay, anchor] as const)

      await show(page)
      const before = await seat([selector, trigger])
      expect(before.gap).toBeGreaterThanOrEqual(0)
      expect(before.gap).toBeLessThan(24)

      // The surface slides up under a transform, which silently becomes the containing block
      // for every fixed overlay inside it. Uncorrected, that same `bottom` throws the panel a
      // keyboard's height up the screen - which is the whole report, and at a 300px inset it
      // would put this panel's bottom edge above the top of the window.
      await page.evaluate(() => window.openKeyboard(300))
      await expect.poll(async () => (await seat([selector, trigger])).gap).toBe(before.gap)
      const after = await seat([selector, trigger])
      expect(after.top).toBeGreaterThanOrEqual(0)
      expect(after.bottom).toBeGreaterThan(0)
      // It really did move with the rail rather than merely staying put.
      expect(after.bottom).toBeLessThan(before.bottom)
    })
  }
})

test('a configured text chip sizes to its label instead of to a fixed target width', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 420 })
  await expect(page.locator(MORE)).toHaveCount(0)
  const [short, long] = await Promise.all([
    page.locator(`${STRIP} > [data-key="learn"]`).boundingBox(),
    page.locator(`${STRIP} > [data-key="commit-and-push"]`).boundingBox(),
  ])
  // The bug this replaces: both were the same 74px box, so a five-character skill wore the
  // width of the longest built-in label beside it.
  expect(short!.width).toBeLessThan(long!.width)
  // Still a real target rather than shrink-wrapped to the glyphs.
  expect(short!.width).toBeGreaterThanOrEqual(30)
})
