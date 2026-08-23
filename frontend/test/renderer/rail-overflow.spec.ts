import { expect, test, type Page } from 'playwright/test'

/**
 * The pinned rail drawer and its complete-row popover, against real geometry and compositing.
 *
 * Every property here is decided outside any one module and is invisible to a unit test:
 * the drawer's width is a CSS variable, the passive edge glows follow real overflow, the
 * popover's glass is the browser's own `backdrop-filter` over whatever the terminal is
 * showing, and "a drop-up opened from inside the panel does not dismiss it" is a race
 * between two independent `window` listeners.
 *
 * The failures this exists to catch are all quiet ones: a drawer that shifts between rows,
 * a chip silently lost between the row and the panel, a panel that closes on the
 * first of a two-click confirm, and a glass that reads fine over a dark buffer and turns to
 * grey-on-grey the moment someone opens a white diff.
 */

const MORE = '.rail-more'
const PANEL = '.rail-overflow-popover'
const GRID = '.rail-overflow-grid'
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

/** Open a drop-up from a chip inside the panel, and wait for it to be placed. Placement and
 *  the drop-up's own Escape listener are installed by the same effect, so a spec that acted
 *  on `toBeVisible` alone would press Escape into a panel not yet listening for it. */
async function openDropup(page: Page) {
  await page.click(`${GRID} [data-key="clip"]`)
  await expect(page.locator(DROPUP)).toBeVisible()
  await expect.poll(async () => page.locator(DROPUP).evaluate(el => (el as HTMLElement).style.left)).not.toBe('')
}

test('the drawer count and popover both cover the complete row', async ({ page }) => {
  const inRow = await page.locator(ROW_CHIPS).count()
  expect(inRow).toBeGreaterThan(0)
  await expect(page.locator('.rail-more-count')).toHaveText(String(inRow))

  await open(page)
  expect(await page.locator(`${GRID} > *`).count()).toBe(inRow)
  await expect(page.locator(`${GRID} > [data-key="learn"]`)).toHaveCount(1)
  await expect(page.locator(`${GRID} > [data-key="endSession"]`)).toHaveCount(1)
})

test('the row scrolls while the drawer never pans away', async ({ page }) => {
  const strip = page.locator(STRIP)
  const scrolls = await strip.evaluate(el => el.scrollWidth - el.clientWidth)
  expect(scrolls).toBeGreaterThan(1)
  // The chip is fixed furniture outside the scroller: panning the chips moves none of it.
  const before = await page.locator(MORE).boundingBox()
  await strip.evaluate(el => { el.scrollLeft = 200 })
  const after = await page.locator(MORE).boundingBox()
  expect(Math.abs(before!.x - after!.x)).toBeLessThanOrEqual(1)
  await strip.evaluate(el => { el.scrollLeft = 0 })
})

test('the overflow chip sits on the row\'s trailing edge at every width', async ({ page }) => {
  // Every edge read inside one evaluate. Separate `boundingBox()` calls resolve at
  // different moments, and a viewport change between two of them reports one box from
  // before the relayout and one from after - which reads as a real geometry failure.
  const measure = () => page.evaluate(() => {
    const row = document.querySelector<HTMLElement>('.rail-row')!
    const more = row.querySelector<HTMLElement>('.rail-more')!
    const cluster = row.querySelector<HTMLElement>('.rail-row-trailing')!
    return {
      moreRight: more.getBoundingClientRect().right,
      rowRight: row.getBoundingClientRect().right,
      clusterPad: Number.parseFloat(getComputedStyle(cluster).paddingRight) || 0,
    }
  })
  for (const width of [420, 520, 760]) {
    await page.setViewportSize({ width, height: 420 })
    await expect(page.locator(MORE)).toBeVisible()
    // Polled, because a viewport change is not settled by the time the chip is visible and
    // a single read can catch the row mid-relayout.
    await expect.poll(async () => {
      const settling = await measure()
      return Math.round(settling.rowRight - settling.clusterPad - settling.moreRight)
    }, { message: `the cluster is off the trailing edge at ${width}px` }).toBe(0)
  }
})

test('a row with room for everything keeps its permanent drawer route', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 420 })
  await expect(page.locator(MORE)).toBeVisible()
  await expect(page.locator('.rail-more-count')).toHaveText(String(await page.locator(ROW_CHIPS).count()))
  const scrolls = await page.locator(STRIP).evaluate(el => el.scrollWidth - el.clientWidth)
  expect(scrolls).toBeLessThanOrEqual(1)
})

/** Each row's right wedge paired with the strip it is supposed to be marking the end of. */
async function wedgesAgainstStrips(page: Page) {
  await expect(page.locator('.rail-row')).toHaveCount(2)
  await expect(page.locator('.terminal-action-rows .overflow-rail-right')).toHaveCount(2)
  return page.locator('.terminal-action-rows > .rail-row').evaluateAll(rows => rows.map(row => ({
    wedge: Math.round(row.querySelector('.overflow-rail-right')!.getBoundingClientRect().right),
    strip: Math.round(row.querySelector('.terminal-action-scroll')!.getBoundingClientRect().right),
  })))
}

test('a right wedge marks its own strip, not a column shared with the row above', async ({ page }) => {
  // The wedge says "content continues past here", so "here" has to be where this row's
  // content is actually cut. Positioned from the *row* edge instead, it had to name the
  // trailing cluster's width — and a row carrying status text has a wider one, so the wedge
  // was drawn past its strip, over furniture that answers to no tap. That is the bug: not a
  // background painted over a chip, but an indicator standing off the end of its own rail.
  await page.goto('/rail-overflow-harness.html?rows=2')
  await expect(page.locator('.rail-row-trailing > span')).toHaveText(['Copied'])
  const rows = await wedgesAgainstStrips(page)
  for (const row of rows) expect(row.wedge).toBe(row.strip - 1)
  // And the two rows genuinely disagree here, so this is not the aligned case restated:
  // the status row's strip ends earlier, and its wedge goes with it.
  expect(rows[1].wedge).toBeLessThan(rows[0].wedge)
})

test('rows whose trailing furniture matches keep their wedges in one column', async ({ page }) => {
  // The alignment the old row-relative rule was reaching for, now falling out of the strips
  // themselves — and reaching further than that rule did, since it holds for the empty
  // status string production passes on the readout row nearly all the time.
  await page.goto('/rail-overflow-harness.html?rows=2&status=')
  await expect(page.locator('.rail-row-trailing > span')).toHaveCount(0)
  const rows = await wedgesAgainstStrips(page)
  for (const row of rows) expect(row.wedge).toBe(row.strip - 1)
  expect(rows[1].wedge).toBe(rows[0].wedge)
})

test('an empty status readout gives its width back to the chips', async ({ page }) => {
  // The caller marks the readout row by passing a string, and that string is empty unless
  // there is something to say. Rendered anyway it was a silent tax on the busiest row: its
  // own padding plus the cluster's gap, taken out of the scrolling strip.
  await expect(page.locator('.rail-row-trailing > span')).toHaveCount(0)
  const gap = await page.locator('.rail-row').evaluate(row => {
    const strip = row.querySelector('.terminal-action-scroll')!.getBoundingClientRect()
    return Math.round(row.querySelector('.rail-more')!.getBoundingClientRect().left - strip.right)
  })
  expect(gap).toBe(0)
})

test('the drawer count is the stable full-row count at every width', async ({ page }) => {
  const count = async () => Number(await page.locator('.rail-more-count').textContent())
  const narrow = await count()
  await page.setViewportSize({ width: 900, height: 420 })
  await expect.poll(count).toBe(narrow)
  await page.setViewportSize({ width: 420, height: 420 })
  await expect.poll(count).toBe(narrow)
})

test('the panel opens upward and lands on the rail\'s trailing edge, inside the viewport', async ({ page }) => {
  await open(page)
  const [box, more, viewport] = await Promise.all([
    page.locator(PANEL).boundingBox(),
    page.locator(MORE).boundingBox(),
    page.evaluate(() => ({ width: window.innerWidth, height: window.innerHeight })),
  ])
  // The panel and permanent drawer share the rail's trailing edge.
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

test('the panel offers full configuration from its footer and closes as it hands over', async ({ page }) => {
  await open(page)
  await expect(page.locator(`${PANEL} > header`)).toHaveCount(0)
  await expect(page.locator(`${GRID} .rail-overflow-configure`)).toHaveCount(0)
  await expect(page.locator('.rail-overflow-actions .rail-overflow-configure')).toBeVisible()
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
  await expect(page.locator(MORE)).toBeVisible()
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
