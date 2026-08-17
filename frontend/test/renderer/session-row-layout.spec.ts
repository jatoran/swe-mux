import { expect, test } from 'playwright/test'

/**
 * The state indicator's placement is pure CSS, so nothing else can catch it.
 *
 * Two regressions live here. The tab thread is drawn through the sessions' own
 * status dots, with its x and its gap expressed in the same variables as the
 * indicator's box — restated as pixels, it stopped covering the dot the moment
 * the indicator changed size and painted a blue stripe straight across it. And
 * the indicator belongs to the *title* line, not to the middle of a two-line
 * row, which is where centring it put it.
 */

interface Box { x: number; y: number; width: number; height: number }
const centerX = (box: Box) => box.x + box.width / 2
const centerY = (box: Box) => box.y + box.height / 2

async function geometry(page: import('playwright/test').Page) {
  return page.evaluate(() => {
    const box = (element: Element | null) => {
      const rect = (element as HTMLElement).getBoundingClientRect()
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
    }
    const branch = document.querySelectorAll('.layout-branch')[1]
    const row = branch.querySelector('.session-row')!
    const thread = getComputedStyle(branch, ':after')
    const threadTop = getComputedStyle(branch, ':before')
    const branchBox = box(branch)
    // The core path is the filled shape a user reads as "the dot"; the element
    // box around it is larger because it also has to hold the context gauge.
    const core = (row.querySelector('.ind-core') as SVGGraphicsElement).getBoundingClientRect()
    return {
      indicator: box(row.querySelector('.state-indicator')),
      core: { x: core.x, y: core.y, width: core.width, height: core.height },
      title: box(row.querySelector('.row-line.top')),
      bottom: box(row.querySelector('.row-line.bottom')),
      row: box(row),
      branch: branchBox,
      threadLeft: parseFloat(thread.left),
      threadGapTop: parseFloat(threadTop.height),
      threadGapBottom: parseFloat(thread.top),
    }
  })
}

test('the thread runs through the indicator instead of across it', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html')
  const g = await geometry(page)

  const threadCenter = g.branch.x + g.threadLeft + 1
  expect(Math.abs(threadCenter - centerX(g.indicator))).toBeLessThanOrEqual(0.6)

  // The gap the thread leaves must contain the whole indicator box, or a segment
  // is drawn over the dot whose colour is the status being reported.
  const gapTop = g.branch.y + g.threadGapTop
  const gapBottom = g.branch.y + g.threadGapBottom
  expect(gapTop).toBeLessThanOrEqual(g.indicator.y + 0.6)
  expect(gapBottom).toBeGreaterThanOrEqual(g.indicator.y + g.indicator.height - 0.6)
})

test('the indicator is centred on the title line, not on the row', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html')
  const g = await geometry(page)

  expect(Math.abs(centerY(g.indicator) - centerY(g.title))).toBeLessThanOrEqual(1)
  // Centring on the row would land it between the two lines instead.
  expect(centerY(g.indicator)).toBeLessThan(g.bottom.y)
})

test('the indicator sits inside its gutter and never overlaps the text', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html')
  const g = await geometry(page)

  expect(g.indicator.x).toBeGreaterThanOrEqual(g.row.x)
  expect(g.indicator.x + g.indicator.width).toBeLessThanOrEqual(g.title.x + 0.5)
  expect(g.indicator.y + g.indicator.height).toBeLessThanOrEqual(g.row.y + g.row.height)
})

/**
 * The indicator's size is user-configurable per device class, and everything
 * around it is expressed as `--session-dot` so that one number moves the gutter
 * column, the thread, the title line, and the row's own height together.
 *
 * The hazard the bounds exist to contain is a row whose height was a fixed 40px:
 * at the top of the range the indicator was taller than the box drawn for it, so
 * the dot spilled into the row beneath. Measured at both endpoints because only
 * a real layout can answer whether it fits.
 */
for (const size of [10, 24]) {
  test(`a ${size}px indicator still fits its row, gutter, and thread`, async ({ page }) => {
    await page.setViewportSize({ width: 900, height: 600 })
    await page.goto('/session-row-harness.html')
    await page.evaluate(px => {
      document.documentElement.style.setProperty('--session-dot', `${px}px`)
    }, size)
    const g = await geometry(page)

    expect(Math.abs(g.indicator.width - size)).toBeLessThanOrEqual(0.6)
    // Contained vertically: the row grew with it rather than clipping it.
    expect(g.indicator.y).toBeGreaterThanOrEqual(g.row.y - 0.5)
    expect(g.indicator.y + g.indicator.height).toBeLessThanOrEqual(g.row.y + g.row.height + 0.5)
    // Contained horizontally: the gutter column is the indicator, so the text
    // must still start beyond it at any size.
    expect(g.indicator.x).toBeGreaterThanOrEqual(g.row.x)
    expect(g.indicator.x + g.indicator.width).toBeLessThanOrEqual(g.title.x + 0.5)
    // Still aligned to the title line rather than drifting toward the row centre.
    expect(Math.abs(centerY(g.indicator) - centerY(g.title))).toBeLessThanOrEqual(1)
    // And the thread still runs through the dot instead of across it.
    const threadCenter = g.branch.x + g.threadLeft + 1
    expect(Math.abs(threadCenter - centerX(g.indicator))).toBeLessThanOrEqual(0.6)
    expect(g.branch.y + g.threadGapTop).toBeLessThanOrEqual(g.indicator.y + 0.6)
    expect(g.branch.y + g.threadGapBottom)
      .toBeGreaterThanOrEqual(g.indicator.y + g.indicator.height - 0.6)
  })
}

test('the visible dot is larger than the 6px one it replaced', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html')
  const g = await geometry(page)

  expect(g.core.width).toBeGreaterThan(6.5)
  expect(g.core.height).toBeGreaterThan(6.5)
  // And still inside its own box, gauge included.
  expect(g.core.width).toBeLessThanOrEqual(g.indicator.width)
})

/**
 * As the sidebar narrows the two bottom sections must slide together, meet at a
 * fixed gap, and then run off the right edge — clipped, not collapsed.
 *
 * The regression this pins: with the left section flexible and the right one
 * fixed, the right section squeezed the left one to nothing, so the branch and
 * diff vanished while the duration stayed put. That is the opposite of what a
 * narrowing sidebar should drop.
 */
test('narrowing slides the sections together and clips, never squeezes the left out', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html')

  const measure = async (width: number) => {
    await page.evaluate(w => {
      document.querySelector<HTMLElement>('.sidebar')!.style.width = `${w}px`
    }, width)
    return page.evaluate(() => {
      const row = document.querySelectorAll('.session-row')[0]
      const line = row.querySelector('.row-line.bottom')!
      const box = (el: Element | null) => {
        if (!el) return null
        const r = el.getBoundingClientRect()
        return { x: r.x, width: r.width, right: r.right }
      }
      const left = line.querySelector('.row-section.left') as HTMLElement
      return {
        line: box(line),
        left: box(left),
        right: box(line.querySelector('.row-section.right')),
        leftText: left.innerText.trim(),
        // Equal means the section is showing all of its content; smaller means
        // it was squeezed and is clipping its own tokens.
        leftScroll: left.scrollWidth,
        leftClient: left.clientWidth,
      }
    })
  }

  const wide = await measure(420)
  expect(wide.left!.width).toBeGreaterThan(0)
  expect(wide.left!.right).toBeLessThanOrEqual(wide.right!.x + 0.5)

  const narrow = await measure(200)
  // The left section keeps every token it drew rather than being squeezed and
  // clipping its own content — this is the assertion the old rules failed.
  expect(narrow.leftText.length).toBeGreaterThan(0)
  // Narrower *does* legitimately shrink this section — the container queries
  // shed whole low-priority tokens. What must never happen is the section being
  // squeezed below the tokens it still draws, which is self-clipping.
  expect(narrow.leftClient).toBeGreaterThanOrEqual(narrow.leftScroll)
  // They meet with a gap preserved, and never overlap.
  expect(narrow.left!.right).toBeLessThanOrEqual(narrow.right!.x + 0.5)
  // Overflow leaves the line to the right and is clipped, not wrapped.
  expect(narrow.right!.right).toBeGreaterThan(narrow.line!.x)
  const lineStyle = await page.evaluate(() =>
    getComputedStyle(document.querySelector('.row-line.bottom')!).overflowX)
  expect(lineStyle).toBe('hidden')
})

/**
 * The flag strip: presence marks pinned to the top line's right edge.
 *
 * The regression this pins is the reason the strip exists. Placed after the
 * title, the marks sat inside the section that clips, so a title long enough to
 * fill the sidebar hid every one of them — and the rows with the most to report
 * are the ones with the longest names. Pure CSS, so nothing but a real layout
 * can answer it.
 */
async function stripGeometry(page: import('playwright/test').Page, width: number) {
  await page.evaluate(w => {
    document.querySelector<HTMLElement>('.sidebar')!.style.width = `${w}px`
  }, width)
  return page.evaluate(() => {
    const row = document.querySelector('[data-row="standing"]')!
    const box = (el: Element | null) => {
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { x: r.x, width: r.width, right: r.right }
    }
    const line = row.querySelector('.row-line.top')!
    const title = line.querySelector('.row-title') as HTMLElement
    return {
      row: box(row),
      line: box(line),
      title: box(title),
      strip: box(line.querySelector('.row-section.right')),
      flags: [...line.querySelectorAll('.row-section.right .row-token')].map(token => box(token)),
      actions: box(row.querySelector('.row-actions')),
      // Smaller than its content means the title is ellipsizing, which is the
      // trade the strip is supposed to force.
      titleClient: title.clientWidth,
      titleScroll: title.scrollWidth,
    }
  })
}

for (const width of [420, 240, 180]) {
  test(`at ${width}px the flag strip stays visible and the title yields instead`, async ({ page }) => {
    await page.setViewportSize({ width: 900, height: 600 })
    await page.goto('/session-row-harness.html')
    const g = await stripGeometry(page, width)

    expect(g.flags.length).toBe(3)
    for (const flag of g.flags) {
      expect(flag!.width).toBeGreaterThan(0)
      expect(flag!.x).toBeGreaterThanOrEqual(g.line!.x - 0.5)
      expect(flag!.right).toBeLessThanOrEqual(g.line!.right + 0.5)
    }
    // Pinned to the edge, with the title stopping before it rather than under it.
    expect(g.strip!.right).toBeLessThanOrEqual(g.line!.right + 0.5)
    expect(g.title!.right).toBeLessThanOrEqual(g.strip!.x + 0.5)
  })
}

test('a title too long for the sidebar ellipsizes rather than pushing the flags out', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html')
  const g = await stripGeometry(page, 200)

  expect(g.titleScroll).toBeGreaterThan(g.titleClient)
  const overflow = await page.evaluate(() =>
    getComputedStyle(document.querySelector('[data-row="standing"] .row-title')!).textOverflow)
  expect(overflow).toBe('ellipsis')
})

/**
 * The kill control overlays the row's right edge; it does not clear a lane for
 * itself. It used to widen `.session-copy` while it was shown, which kept it off
 * the flags but re-laid-out the row at the moment the pointer arrived — every
 * token slid left while you were reading them. Covering one token is a smaller
 * loss than moving all of them, so the reserved lane is gone and this test now
 * guards the opposite invariant: hovering changes no geometry at all.
 */
test('the hover-revealed kill control overlays the row without reflowing it', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html')
  const before = await stripGeometry(page, 300)
  await page.hover('[data-row="standing"]')
  const after = await stripGeometry(page, 300)

  expect(after.actions!.width).toBeGreaterThan(0)
  // Nothing under the control moves when it appears.
  expect(after.title).toEqual(before.title)
  expect(after.strip).toEqual(before.strip)
  expect(after.flags).toEqual(before.flags)
  expect(after.titleClient).toBe(before.titleClient)
  // It sits over the row's right edge rather than beyond it.
  expect(after.actions!.right).toBeLessThanOrEqual(after.row!.right + 0.5)
  expect(after.actions!.x).toBeGreaterThan(after.row!.x)
})

/**
 * The working pulse marks the *state*. The context gauge only moves when the
 * conversation grows, so blinking it alongside the core makes a static
 * measurement read as live activity.
 */
test('the pulse animates the core alone, never the context gauge', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html')

  const animations = await page.evaluate(() => {
    // The first harness row is `working` with a context arc.
    const indicator = document.querySelectorAll('.session-row')[0].querySelector('.state-indicator')!
    const name = (el: Element | null) => (el ? getComputedStyle(el).animationName : 'missing')
    return {
      indicator: name(indicator),
      core: name(indicator.querySelector('.ind-core')),
      fill: name(indicator.querySelector('.ind-fill')),
      track: name(indicator.querySelector('.ind-track')),
      working: indicator.classList.contains('working'),
    }
  })

  expect(animations.working).toBe(true)
  expect(animations.core).not.toBe('none')
  expect(animations.indicator).toBe('none')
  expect(animations.fill).toBe('none')
  expect(animations.track).toBe('none')
})

for (const shape of ['hexagon', 'circle', 'square'] as const) {
  test(`the ${shape} indicator stays concentric with its gauge`, async ({ page }) => {
    await page.setViewportSize({ width: 900, height: 600 })
    await page.goto(`/session-row-harness.html?shape=${shape}`)
    const g = await page.evaluate(() => {
      const row = document.querySelectorAll('.session-row')[0]
      const rect = (selector: string) => {
        const element = row.querySelector(selector) as SVGGraphicsElement | null
        if (!element) return null
        const box = element.getBoundingClientRect()
        return { x: box.x, y: box.y, width: box.width, height: box.height }
      }
      return { core: rect('.ind-core'), track: rect('.ind-track') }
    })
    expect(g.core).not.toBeNull()
    expect(g.track).not.toBeNull()
    expect(Math.abs(centerX(g.core!) - centerX(g.track!))).toBeLessThanOrEqual(0.5)
    expect(Math.abs(centerY(g.core!) - centerY(g.track!))).toBeLessThanOrEqual(0.5)
    expect(g.track!.width).toBeGreaterThan(g.core!.width)
  })
}
