import { expect, test } from 'playwright/test'

/**
 * The voice dock's layout contract (`design/features/voice.md`).
 *
 * Three things here are CSS-only and therefore invisible to `tsc` and to the unit suite:
 *
 * 1. The dock floats. It hangs from a zero-height anchor inside the main stage's own grid
 *    cell, so a pane's row count - which *is* the PTY's row count - is byte-identical at
 *    every dock size. A dock that took a workspace track would resize a live agent's
 *    terminal every time the panel opened, and the reflowed scrollback does not come back.
 * 2. Collapsed to the chip, the workspace is completely clear. That was the whole
 *    complaint about the old panel: folding it left a floating header behind.
 * 3. The peek row has no composer but keeps every open confirmation card, buttons and
 *    countdown included. A card the operator can see but not answer is worse than one
 *    they cannot see, because the scheduled ones run on their own.
 */

const bounds = () => {
  const box = (selector: string) => {
    const element = document.querySelector<HTMLElement>(selector)
    if (!element) return null
    const rectangle = element.getBoundingClientRect()
    // A `display:none` dock still has an element; zero area is how it reports itself.
    if (!rectangle.width && !rectangle.height) return null
    const { x, y, width, height } = rectangle
    return { x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height) }
  }
  return {
    pane: box('.terminal-pane')!, bar: box('.pane-bar')!, surface: box('.terminal-surface')!,
    host: box('.terminal-host')!, stage: box('.main-stage')!,
    dock: box('.voice-dock'), control: box('.conversation-talk-toggle')!,
  }
}

test('the dock floats: the terminal is identical at every size', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 760 })
  await page.goto('/voice-dock-harness.html?dock=chip')
  await expect(page.locator('.terminal-surface')).toBeVisible()
  const collapsed = await page.evaluate(bounds)

  for (const dock of ['peek', 'full']) {
    await page.goto(`/voice-dock-harness.html?dock=${dock}`)
    await expect(page.locator('.voice-dock')).toBeVisible()
    const open = await page.evaluate(bounds)
    expect(open.surface, `${dock} moved the terminal surface`).toEqual(collapsed.surface)
    expect(open.host, `${dock} resized the terminal host`).toEqual(collapsed.host)
    expect(open.pane).toEqual(collapsed.pane)
    // Still inside the main stage, hanging from its top rather than from the viewport.
    expect(open.dock!.x).toBeGreaterThanOrEqual(open.stage.x)
    expect(open.dock!.x + open.dock!.width).toBeLessThanOrEqual(open.stage.x + open.stage.width)
    expect(open.dock!.y).toBeGreaterThanOrEqual(open.stage.y)
    expect(open.dock!.y).toBeLessThan(open.stage.y + 40)
  }
})

test('collapsed to the chip, nothing of the panel is left over the workspace', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 760 })
  await page.goto('/voice-dock-harness.html?dock=chip')
  // Not merely small: absent from the layout. The old collapse left its header floating,
  // which is what "it still covers part of the workspace" meant.
  await expect(page.locator('.voice-dock')).toBeHidden()
  const geometry = await page.evaluate(bounds)
  expect(geometry.dock).toBe(null)
  // The way back is the one voice control in the top bar - there is exactly one, and
  // it is the same button that reaches capture behind ctrl+click.
  const control = page.locator('.app-identity .conversation-talk-toggle')
  await expect(control).toBeVisible()
  await expect(control).toHaveAttribute('aria-expanded', 'false')
  expect(await page.locator('.conversation-talk-toggle').count()).toBe(1)
})

test('the peek is one thin row with no composer', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 760 })
  await page.goto('/voice-dock-harness.html?dock=full')
  const full = await page.evaluate(bounds)
  await page.goto('/voice-dock-harness.html?dock=peek')
  await expect(page.locator('.voice-dock')).toBeVisible()
  const peek = await page.evaluate(bounds)
  expect(peek.dock!.height).toBeLessThan(full.dock!.height / 2)
  // No composer at this size, in either body.
  await expect(page.locator('.assistant-input')).toHaveCount(0)
  await expect(page.locator('.dictation-draft')).toHaveCount(0)
  // The newest line is still readable, on one line.
  await expect(page.locator('.assistant-peek-line')).toBeVisible()

  await page.goto('/voice-dock-harness.html?dock=peek&mode=talk')
  await expect(page.locator('.voice-dock-line')).toBeVisible()
  await expect(page.locator('.dictation-draft')).toHaveCount(0)
})

test('an open confirmation card stays answerable at the peek', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 760 })
  await page.goto('/voice-dock-harness.html?dock=peek&card=1')
  const card = page.locator('.assistant-action')
  await expect(card).toBeVisible()
  await expect(card.locator('button.confirm')).toBeVisible()
  await expect(card.locator('button.cancel')).toBeVisible()
  await expect(card.locator('.assistant-countdown')).toBeVisible()
  // And the chip carries the count, so the same card is findable from the collapsed state.
  await page.goto('/voice-dock-harness.html?dock=chip&card=1')
  await expect(page.locator('.voice-dock-badge')).toHaveText('1')
})

test('the size controls and the capture control are different buttons', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 760 })
  await page.goto('/voice-dock-harness.html?dock=full')
  // Capture is running, and both microphones say so - the top-bar control and the
  // panel's own, which is the primary capture control on touch.
  await expect(page.locator('.conversation-talk-toggle.active')).toBeVisible()
  await expect(page.locator('.voice-dock-mic.active')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Stop listening' })).toBeVisible()
  const collapse = page.getByRole('button', { name: 'Collapse the voice panel one step' })
  const expand = page.getByRole('button', { name: 'Expand the voice panel one step' })
  await expect(collapse).toBeEnabled()
  // Full is the top of the range, so only one direction is offered.
  await expect(expand).toBeDisabled()

  await page.goto('/voice-dock-harness.html?dock=chip')
  // Capture is unaffected by the collapse: the mic is still lit with the dock gone.
  await expect(page.locator('.conversation-talk-toggle.active')).toBeVisible()
})

test('the read-aloud tab is a third body and fits without moving the terminal', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 760 })
  await page.goto('/voice-dock-harness.html?dock=full&mode=read')
  await expect(page.locator('.voice-dock.read-mode')).toBeVisible()
  const open = await page.evaluate(bounds)
  await expect(page.locator('.voice-read-controls')).toBeVisible()
  await expect(page.locator('.voice-read-clip')).toHaveCount(3)
  // On-demand generation and the transport are here rather than on a pane: this tab is read
  // aloud's only control surface, and the retired player strip was the only other route to
  // either. `↻ speak` is not gated on the session's mode - the daemon's manual path checks
  // the master switch alone - which is a capability the strip could not offer, because it
  // was drawn only once the mode was already on.
  await expect(page.locator('.voice-read-speak')).toBeVisible()
  await expect(page.locator('.voice-read-now')).toBeVisible()
  // The clip list scrolls inside the panel rather than growing it past its bound.
  const overflow = await page.evaluate(() =>
    getComputedStyle(document.querySelector<HTMLElement>('.voice-read-clips')!).overflowY)
  expect(overflow).toBe('auto')
  // The panel's flexible height belongs to the list and to nothing else. A fixed
  // `auto minmax(0,1fr)` template handed it to whichever child was second instead, so the
  // transport (or an error line) took the panel and left the list in an implicit `auto` row
  // it could not scroll inside. Both facts are geometric, and neither is visible to tsc.
  const stack = await page.evaluate(() => {
    const at = (selector: string) => {
      const { y, height } = document.querySelector<HTMLElement>(selector)!.getBoundingClientRect()
      return { y: Math.round(y), height: Math.round(height) }
    }
    return { panel: at('.voice-read'), controls: at('.voice-read-controls'), now: at('.voice-read-now'), clips: at('.voice-read-clips') }
  })
  expect(stack.now.y).toBeGreaterThanOrEqual(stack.controls.y + stack.controls.height)
  expect(stack.clips.y).toBeGreaterThanOrEqual(stack.now.y + stack.now.height)
  expect(stack.now.height).toBeLessThan(stack.clips.height)
  expect(stack.clips.y + stack.clips.height).toBeLessThanOrEqual(stack.panel.y + stack.panel.height + 1)
  // Same floating contract as every other body: the terminal is untouched.
  await page.goto('/voice-dock-harness.html?dock=chip')
  const collapsed = await page.evaluate(bounds)
  expect(open.surface).toEqual(collapsed.surface)
  expect(open.host).toEqual(collapsed.host)
})

test('the voice control fits the phone toolbar without wrapping it', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 })
  await page.goto('/voice-dock-harness.html?dock=chip&mobile=1')
  const bar = page.locator('.mobile-toolbar')
  await expect(bar).toBeVisible()
  const row = await page.evaluate(() => {
    const toolbar = document.querySelector<HTMLElement>('.mobile-toolbar')!
    const children = [...toolbar.children] as HTMLElement[]
    const boxes = children.map(child => child.getBoundingClientRect())
    return {
      // Centres, not tops: the bar is `align-items:center` and its controls are different
      // heights, so equal tops would be the wrong question. Wrapping moves a centre.
      centres: boxes.map(box => Math.round(box.top + box.height / 2)),
      right: Math.round(Math.max(...boxes.map(box => box.right))),
      barRight: Math.round(toolbar.getBoundingClientRect().right),
      barHeight: Math.round(toolbar.getBoundingClientRect().height),
      control: (() => {
        const { width, height } = toolbar.querySelector('.conversation-talk-toggle')!.getBoundingClientRect()
        return { width: Math.round(width), height: Math.round(height) }
      })(),
    }
  })
  // One row: every control shares a centre line, nothing is pushed past the bar, and the
  // bar itself is still a single row tall.
  expect(new Set(row.centres).size).toBe(1)
  expect(row.right).toBeLessThanOrEqual(row.barRight + 1)
  expect(row.barHeight).toBeLessThan(60)
  // And it is a thumb target, not the 21px desktop box. On touch this button is also
  // the capture shortcut (a hold), so it has to be comfortably holdable.
  expect(row.control.width).toBeGreaterThanOrEqual(36)
  expect(row.control.height).toBeGreaterThanOrEqual(36)
})

test('nothing in the workspace draws over the dock, and every overlay still does', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 760 })
  await page.goto('/voice-dock-harness.html?dock=full')
  const bands = await page.evaluate(() => {
    const anchor = document.querySelector<HTMLElement>('.voice-dock-anchor')!
    const probe = (className: string) => {
      const element = document.createElement('div')
      element.className = className
      document.body.append(element)
      const value = Number(getComputedStyle(element).zIndex)
      element.remove()
      return value
    }
    return {
      dock: Number(getComputedStyle(anchor).zIndex),
      palette: probe('palette-layer'),
      modal: probe('modal-layer'),
      menu: probe('context-menu'),
      // The pane tab strip's scroll arrows. These are the reported defect: the strip sat
      // under the panel while its own left/right buttons drew on top of it.
      railEdge: probe('overflow-rail-edge'),
      paneRing: (() => {
        const stack = document.createElement('div')
        stack.className = 'pane-stack'
        document.body.append(stack)
        const value = Number(getComputedStyle(stack, '::after').zIndex)
        stack.remove()
        return value
      })(),
    }
  })
  expect(bands.dock).toBeGreaterThan(bands.railEdge)
  expect(bands.dock).toBeGreaterThan(bands.paneRing)
  // A dialog the dock paints over is a dialog whose own header swallows taps, so every
  // overlay band still outranks it.
  expect(bands.dock).toBeLessThan(bands.menu)
  expect(bands.dock).toBeLessThan(bands.modal)
  expect(bands.dock).toBeLessThan(bands.palette)
})

test('Talk history collapses inside the dock without moving the terminal', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 760 })
  await page.goto('/voice-dock-harness.html?dock=full&mode=talk')
  const toggle = page.locator('.conversation-history-toggle')
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  const before = await page.evaluate(bounds)
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await expect(page.locator('.conversation-history')).toHaveCount(0)
  const after = await page.evaluate(bounds)
  expect(after.surface).toEqual(before.surface)
  expect(after.host).toEqual(before.host)
})

test('the Talk header keeps transient detail accessible without repeating history text', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 })
  await page.goto('/voice-dock-harness.html?dock=full&mode=talk')
  await expect(page.locator('.dictation-detail')).toHaveCount(0)
  await expect(page.locator('.voice-dock>header .sr-only')).toHaveText('Listening. Say “mux, send” to submit.')
  await expect(page.locator('.dictation-phase').first()).toHaveAttribute('title', /Listening\. Say/)
})

test('the commands button opens the shared catalog above the dock', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 })
  await page.goto('/voice-dock-harness.html?dock=full&mode=talk')
  const before = await page.evaluate(bounds)
  await page.locator('.dictation-actions .voice-commands-open').click()
  const dialog = page.locator('.voice-command-dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('append without sending')).toBeVisible()
  const box = await dialog.boundingBox()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(390)
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  const after = await page.evaluate(bounds)
  expect(after.surface).toEqual(before.surface)
  expect(after.host).toEqual(before.host)
})
