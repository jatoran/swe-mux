import { expect, test, type Page } from 'playwright/test'
import {
  DEFAULT_DOT_SIZE_DESKTOP, DEFAULT_DOT_SIZE_MOBILE,
} from '../../src/sessionRowConfig'

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

/**
 * Set the sidebar's width and wait for the width ladder to have been recomputed
 * from it.
 *
 * The budget is measured in a `ResizeObserver` and the tokens are rebuilt from
 * that measurement, so a width set in one frame does not reach the row until the
 * next. The harness publishes a render counter for exactly this: waiting on the
 * thing that has to happen beats waiting a guessed number of frames.
 *
 * Re-applying the width the sidebar already has resizes nothing, so the observer
 * never fires and there is nothing to wait for. Waiting anyway would hang, which
 * is what a caller measuring the same width twice — before and after a hover —
 * actually does.
 */
async function resizeSidebar(page: Page, width: number) {
  // The counter is read in the SAME evaluate that applies the width. Read in a
  // second round trip it can already hold the value the resize produced, and the
  // wait below then never sees it change.
  const before = await page.evaluate(w => {
    const sidebar = document.querySelector<HTMLElement>('.sidebar')!
    const target = `${w}px`
    if (sidebar.style.width === target) return null
    const previous = document.documentElement.dataset.rowRender
    sidebar.style.width = target
    return previous ?? ''
  }, width)
  if (before === null) return
  await page.waitForFunction(
    previous => document.documentElement.dataset.rowRender !== previous,
    before,
  )
}

async function geometry(page: Page) {
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
 * Which section yields as the sidebar narrows, and what that looks like.
 *
 * This assertion set is the REVERSE of the one it replaces, deliberately. The old
 * rule gave the left section absolute precedence: neither section shrank, and the
 * right one was pushed off the line's edge and clipped. Measured at the 190px
 * minimum, a 22-character worktree on the left held a fixed 116px while 49 of the
 * model's 68px were cut off the right edge mid-glyph, with no ellipsis — the box
 * was never squeezed, so `text-overflow` never engaged.
 *
 * The failure the old rule guarded against — a fixed right section squeezing the
 * left one out of existence — is now prevented by `--row-token-floor` rather than
 * by making the left unshrinkable. Both bounds are asserted here, so restoring
 * either half of the old rule fails: the left must keep a floor, and the right
 * must stay inside the line.
 */
test('narrowing truncates the left section and never pushes the right off the row', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html?layout=worktree-model')

  const measure = async (width: number) => {
    await resizeSidebar(page, width)
    return page.evaluate(() => {
      const row = document.querySelector('[data-row="working"]')!
      const line = row.querySelector('.row-line.bottom')!
      const box = (el: Element | null) => {
        if (!el) return null
        const r = el.getBoundingClientRect()
        return { x: r.x, width: r.width, right: r.right }
      }
      const left = line.querySelector('.row-section.left') as HTMLElement
      const right = line.querySelector('.row-section.right') as HTMLElement
      return {
        line: box(line),
        left: box(left),
        right: box(right),
        leftText: left.innerText.trim(),
        rightText: right.innerText.trim(),
        leftWidth: left.getBoundingClientRect().width,
        // A collapsed token renders the field's mark instead of its value.
        leftIsIcon: !!left.querySelector('.row-icon'),
        // Truthy only while the browser is actually ellipsizing the value, which is
        // the rung between the full value and the mark.
        leftEllipsized: (() => {
          const text = left.querySelector('.row-text') as HTMLElement | null
          return !!text && text.scrollWidth > text.clientWidth
        })(),
      }
    })
  }

  const wide = await measure(420)
  expect(wide.leftText).toBe('feat-tokenizer-rewrite')
  expect(wide.rightText).toBe('5-codex')
  expect(wide.left!.right).toBeLessThanOrEqual(wide.right!.x + 0.5)

  // The regression: at the narrowest the sidebar can be dragged, the right
  // section must be fully inside the line rather than clipped by its edge.
  const narrow = await measure(190)
  expect(narrow.rightText).toBe('5-codex')
  expect(narrow.right!.right).toBeLessThanOrEqual(narrow.line!.right + 0.5)
  expect(narrow.right!.x).toBeGreaterThanOrEqual(narrow.line!.x)
  // And they still never overlap.
  expect(narrow.left!.right).toBeLessThanOrEqual(narrow.right!.x + 0.5)
  // The left section is the one that gave up room, and it did it by ellipsizing
  // rather than by being collapsed: two tokens still fit at this width once the
  // yielding one may lose its tail, so the mark is not needed yet.
  expect(narrow.leftWidth).toBeLessThan(wide.leftWidth)
  expect(narrow.leftEllipsized).toBe(true)
  expect(narrow.leftIsIcon).toBe(false)

  // The line clips rather than wrapping, at every width.
  const lineStyle = await page.evaluate(() =>
    getComputedStyle(document.querySelector('.row-line.bottom')!).overflowX)
  expect(lineStyle).toBe('hidden')
})

/**
 * The icon rung, which is only reachable when several fields are competing: two
 * tokens fit at any draggable width once each section's yielding token may
 * ellipsize, so a mark stands in for a value on a crowded line and nowhere else.
 */
test('a crowded line collapses low-priority values to their marks', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html?layout=crowded')
  await resizeSidebar(page, 190)

  const drawn = await page.evaluate(() => {
    const line = document.querySelector('[data-row="working"] .row-line.bottom')!
    const left = line.querySelector('.row-section.left') as HTMLElement
    const right = line.querySelector('.row-section.right') as HTMLElement
    return {
      icons: [...left.querySelectorAll('.row-icon')].map(icon => icon.textContent),
      leftRight: left.getBoundingClientRect().right,
      rightX: right.getBoundingClientRect().x,
      rightInside: right.getBoundingClientRect().right <= line.getBoundingClientRect().right + 0.5,
      // Whatever survives on the right, the section is not empty: the engine may
      // never delete the last token a section holds.
      rightTokens: right.querySelectorAll('.row-token').length,
      leftTokens: left.querySelectorAll('.row-token').length,
    }
  })
  // The branch carries `⎇` and is the lowest-priority left field that owns a mark.
  expect(drawn.icons).toContain('⎇')
  expect(drawn.leftTokens).toBeGreaterThan(0)
  expect(drawn.rightTokens).toBeGreaterThan(0)
  expect(drawn.rightInside).toBe(true)
  expect(drawn.leftRight).toBeLessThanOrEqual(drawn.rightX + 0.5)
})

/**
 * The floor the old rule's failure mode is now prevented by. A right section laid
 * out first must not be able to squeeze the left one to nothing, which is exactly
 * what a fixed right beside a flexible left does without a `min-width`.
 */
test('the left section keeps a floor rather than being squeezed away', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html?layout=worktree-model')
  await resizeSidebar(page, 190)

  const floor = await page.evaluate(() => {
    const line = document.querySelector('[data-row="working"] .row-line.bottom')!
    const left = line.querySelector('.row-section.left') as HTMLElement
    const probe = document.createElement('span')
    probe.style.cssText = 'position:absolute;visibility:hidden;white-space:pre'
    probe.textContent = '000000'
    line.appendChild(probe)
    const sixChars = probe.getBoundingClientRect().width
    probe.remove()
    return { leftWidth: left.getBoundingClientRect().width, sixChars }
  })
  // Six characters of the line's own type is the engine's `ROW_MIN_CHARS`; the
  // section is at or above it, never collapsed to zero.
  expect(floor.leftWidth).toBeGreaterThanOrEqual(floor.sixChars * 0.9)
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
async function stripGeometry(page: Page, width: number) {
  await resizeSidebar(page, width)
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

    // broadcast, read aloud, standing activity, unsent input — every mark the strip can
    // hold, on the one row whose title cannot fit.
    expect(g.flags.length).toBe(4)
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

/**
 * The shipped default, drawn rather than asserted as an object.
 *
 * A default that renders wrong is worse than the one it replaced, and everything
 * that could go wrong here is invisible to the unit suite: whether the always-on
 * time and model actually line up into two columns down the sidebar, whether the
 * glyph-decorated worktree fits between them at the width the sidebar is usually
 * left at, and whether the 21px indicator drives a row tall enough to hold both
 * lines. So it is measured in a browser, at the width a person really uses.
 */
async function defaultRows(page: Page, width: number) {
  await resizeSidebar(page, width)
  return page.evaluate(() => {
    const read = (id: string) => {
      const row = document.querySelector(`[data-row="${id}"]`)!
      const line = row.querySelector('.row-line.bottom')!
      const section = (side: string) => {
        const element = line.querySelector(`.row-section.${side}`) as HTMLElement | null
        if (!element) return null
        const rect = element.getBoundingClientRect()
        return {
          // Normalized because `innerText` breaks each inline token onto its own
          // line; the assertion is about which tokens drew and in what order.
          text: element.innerText.replace(/\s+/g, ' ').trim(),
          x: rect.x, right: rect.right, width: rect.width,
        }
      }
      return {
        left: section('left'), right: section('right'),
        row: row.getBoundingClientRect(),
        top: row.querySelector('.row-line.top')!.getBoundingClientRect(),
        bottom: line.getBoundingClientRect(),
      }
    }
    return { working: read('working'), ready: read('ready'), standing: read('standing') }
  })
}

test('the shipped default draws two columns with the conditional fields between them', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 })
  await page.goto('/session-row-harness.html')

  // Roomy first, so the assertion is about what the layout *says* rather than
  // about the width ladder.
  const wide = await defaultRows(page, 420)

  // Left: the always-on time, then the worktree carrying its glyph, then the
  // queue depth — and no state word, because `state` ships placed in the mode
  // that never draws. The branch is absent because it is not placed at all.
  expect(wide.working.left!.text).toBe('22m · ⌂ feat-tokenizer-rewrite · ⋮2')
  expect(wide.working.left!.text).not.toContain('working')
  expect(wide.working.left!.text).not.toContain('feat-tokenizer ')
  // Right: the model, always, on every row that has one.
  expect(wide.working.right!.text).toBe('5-codex')
  expect(wide.ready.right!.text).toBe('opus')
  expect(wide.standing.right!.text).toBe('opus')
  // A ready row still draws its time, which is the point of `always`: the column
  // exists on every row rather than appearing when a session happens to be slow.
  expect(wide.ready.left!.text).toBe('1m12')

  // The column the always-on model buys: every row's right section ends on the
  // same edge, and none of them collides with the left.
  const rights = [wide.working, wide.ready, wide.standing].map(row => row.right!.right)
  for (const edge of rights) expect(Math.abs(edge - rights[0])).toBeLessThanOrEqual(0.5)
  for (const row of [wide.working, wide.ready, wide.standing]) {
    expect(row.left!.right).toBeLessThanOrEqual(row.right!.x + 0.5)
  }

  // At the sidebar's own default width the line is over budget, and what gives is
  // the worktree's value rather than the model or the time: the ladder collapses
  // it to its mark and every placed fact is still on the row. This is the whole
  // reason the default may hold three left-hand fields at all.
  const snug = await defaultRows(page, 254)
  expect(snug.working.left!.text).toBe('22m · ⌂ · ⋮2')
  expect(snug.working.right!.text).toBe('5-codex')
  expect(snug.working.left!.right).toBeLessThanOrEqual(snug.working.right!.x + 0.5)

  // The 21px indicator drives the row's height, so both lines must still fit
  // inside the row rather than the row clipping one of them.
  for (const row of [snug.working, snug.ready, snug.standing]) {
    expect(row.top.height).toBeGreaterThan(0)
    expect(row.bottom.height).toBeGreaterThan(0)
    expect(row.row.height).toBeGreaterThanOrEqual(row.top.height + row.bottom.height - 0.5)
  }
  const dot = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue('--session-dot').trim())
  expect(dot).toBe(`${DEFAULT_DOT_SIZE_DESKTOP}px`)
})

/**
 * `style.css` keeps its own copy of both default indicator sizes, deliberately:
 * they are what a page whose settings have not resolved — or whose daemon is
 * unreachable — falls back to, and no stored blob is available at that moment.
 *
 * Nothing checked the two copies against each other, so moving the default in
 * TypeScript alone would give a fresh install one size before its settings load
 * and another after: a visible jump on every boot, and invisible to every unit
 * test, because the fallback is precisely the state where the model is not
 * consulted. Asserted here rather than by matching the stylesheet's text, since
 * a browser can simply be asked what the declaration resolved to.
 */
for (const [label, width, expected] of [
  ['desktop', 900, DEFAULT_DOT_SIZE_DESKTOP],
  ['mobile', 700, DEFAULT_DOT_SIZE_MOBILE],
] as const) {
  test(`the ${label} stylesheet fallback is the ${label} default the model ships`, async ({ page }) => {
    await page.setViewportSize({ width, height: 600 })
    await page.goto('/session-row-harness.html')
    const dot = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue('--session-dot').trim())
    expect(dot).toBe(`${expected}px`)
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
