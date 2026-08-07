import { expect, test } from 'playwright/test'

test('WebGL survives repeated pane switches, resizes, and concurrent output', async ({ page }, testInfo) => {
  const rendererErrors: string[] = []
  page.on('pageerror', error => rendererErrors.push(error.stack ?? error.message))
  page.on('console', message => {
    if (message.type() === 'error') rendererErrors.push(message.text())
  })

  await page.goto('/renderer-harness.html')
  const result = await page.evaluate(() => window.runTerminalRendererStress())
  expect(result.renderer).toBe('webgl')
  expect(result.cols).toBeGreaterThan(0)
  expect(result.rows).toBeGreaterThan(0)

  const terminal = page.locator('#terminal')
  const first = await terminal.screenshot()
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
  const second = await terminal.screenshot()
  const pixelRatios = await page.evaluate(async ({ firstPng, secondPng }) => {
    const pixels = async (base64: string) => {
      const bitmap = await createImageBitmap(await (await fetch(`data:image/png;base64,${base64}`)).blob())
      const canvas = new OffscreenCanvas(bitmap.width, bitmap.height)
      const context = canvas.getContext('2d')!
      context.drawImage(bitmap, 0, 0)
      return context.getImageData(0, 0, bitmap.width, bitmap.height).data
    }
    const firstPixels = await pixels(firstPng)
    const secondPixels = await pixels(secondPng)
    let changed = 0
    let foreground = 0
    for (let index = 0; index < firstPixels.length; index += 4) {
      if (Math.abs(firstPixels[index] - secondPixels[index]) > 4 ||
          Math.abs(firstPixels[index + 1] - secondPixels[index + 1]) > 4 ||
          Math.abs(firstPixels[index + 2] - secondPixels[index + 2]) > 4 ||
          Math.abs(firstPixels[index + 3] - secondPixels[index + 3]) > 4) changed += 1
      if (Math.abs(firstPixels[index] - 9) > 8 ||
          Math.abs(firstPixels[index + 1] - 10) > 8 ||
          Math.abs(firstPixels[index + 2] - 12) > 8) foreground += 1
    }
    const count = firstPixels.length / 4
    return { changed: changed / count, foreground: foreground / count }
  }, { firstPng: first.toString('base64'), secondPng: second.toString('base64') })

  // SwiftShader can vary a small number of antialiased edge pixels between captures.
  // Stale rows alter a substantial portion of the viewport, so keep a narrow 0.5% ceiling.
  if (pixelRatios.changed >= 0.005 || pixelRatios.foreground <= 0.01) {
    await testInfo.attach('first-terminal-frame', { body: first, contentType: 'image/png' })
    await testInfo.attach('second-terminal-frame', { body: second, contentType: 'image/png' })
  }
  expect(pixelRatios.foreground, 'terminal must retain visible text after hidden-pane stress').toBeGreaterThan(0.01)
  expect(pixelRatios.changed, 'terminal pixels should remain stable after the render queue drains').toBeLessThan(0.005)
  expect(rendererErrors.filter(error => /WebglRenderer|_updateModel|addon-webgl/i.test(error))).toEqual([])
})

test('same-grid reflow restores a stale DOM renderer surface', async ({ page }) => {
  await page.goto('/renderer-harness.html')
  const result = await page.evaluate(() => window.runTerminalDomDimensionRepair())

  expect(result.cols).toBeGreaterThan(0)
  expect(result.rows).toBeGreaterThan(0)
  expect(result.afterFit).not.toEqual(result.before)
  expect(result.afterReflow).toEqual(result.before)
})

test('mobile IME focus initializes a visible Codex DOM cursor', async ({ page }) => {
  await page.goto('/renderer-harness.html')
  const result = await page.evaluate(() => window.runTerminalMobileCursorInitialization())

  expect(result.beforeInitialized, 'a normal-screen Codex frame alone does not initialize xterm cursor rendering').toBe(false)
  expect(result.afterInitialized, 'the cursor is rendered after the one-time xterm focus bootstrap').toBe(true)
  expect(result.inactiveBar, 'the externally-focused mobile terminal uses its inactive bar cursor').toBe(true)
  expect(result.mobileInputFocused, 'the native mobile IME bridge keeps input focus').toBe(true)
})

test('a synthetic touch tap reaches xterm mouse tracking as press and release reports',async({page})=>{
  await page.goto('/renderer-harness.html')
  const result=await page.evaluate(()=>window.runTerminalSyntheticMouseTap())

  expect(result.tracking).toBe('vt200')
  expect(result.reports).toHaveLength(2)
  expect(result.reports[0]).toMatch(/^\x1b\[<0;\d+;\d+M$/)
  expect(result.reports[1]).toMatch(/^\x1b\[<0;\d+;\d+m$/)
})

test('an unstyled Codex composer resolves against xterm buffer cells',async({page})=>{
  await page.goto('/renderer-harness.html')
  const result=await page.evaluate(()=>window.runUnstyledCodexCaretResolution())

  expect(result.prefixBgMode).toBe(0)
  expect(result.current).not.toBeNull()
  expect(result.target).toEqual({column:4,row:result.current!.row})
})

test('a warm pane keeps rendering under visibility:hidden; display:none pauses it',async({page})=>{
  // Pins the deliberate cost of the `.pane-warm` CSS: xterm's render pause is driven
  // by an IntersectionObserver, which is geometric, so a measurable hidden box keeps
  // the renderer live for every write while a zero-sized one pauses it. If a future
  // xterm gains real visibility awareness, this test failing is the signal that the
  // warm-pane render cost has disappeared and any mitigation can be removed.
  await page.goto('/renderer-harness.html')
  const result=await page.evaluate(()=>window.runWarmPaneRenderCost())

  expect(result.writes).toBeGreaterThan(0)
  expect(result.warmRenders,'visibility:hidden warm pane must keep rendering').toBeGreaterThan(0)
  expect(result.displayNoneRenders,'display:none must pause the renderer').toBe(0)
})

test('repeated active/warm cycling with streaming output converges on a correct pane',async({page})=>{
  await page.goto('/renderer-harness.html')
  const result=await page.evaluate(()=>window.runWarmPaneCycleSoak())

  expect(result.cols,'grid must match a fresh fit of the final box').toBe(result.proposedCols)
  expect(result.rows).toBe(result.proposedRows)
  expect(result.renderedRowElements,'DOM renderer must carry exactly the viewport rows').toBe(result.rows)
  expect(result.viewportY,'the tail must be reachable after the last reveal').toBe(result.baseY)
  expect(result.scrollbackLines,'output written while warm must be in scrollback').toBeGreaterThan(200)
})

test('agent width policies keep Codex above 80 columns and Claude near 120',async({page})=>{
  await page.goto('/renderer-harness.html')
  const result=await page.evaluate(()=>window.runTerminalWidthPolicies())

  expect(result.codex.initialCols).toBeLessThan(80)
  expect(result.codex.fontSize).toBeLessThan(11)
  expect(result.codex.finalCols).toBeGreaterThanOrEqual(80)
  expect(result.claude.cols).toBeGreaterThanOrEqual(118)
  expect(result.claude.cols).toBeLessThanOrEqual(121)
  expect(result.claude.hostWidth).toBeLessThan(1000)
  expect(new Set(result.claude.repeatedCols).size, 'repeated fits must not consume Claude columns').toBe(1)
  expect(new Set(result.claude.repeatedWidths).size, 'the centered Claude host must keep a definite width').toBe(1)
})

test('restoring the font is enough to leave a letterbox; no renderer reflow is needed', async ({ page }) => {
  // Pins the reason `applyGeometry` does not call `reflowVisibleTerminalRenderer` when it
  // drops a letterbox. The stale-surface repair above is real, but it is for a surface
  // resized underneath xterm — a *font* change re-measures on its own, at an unchanged
  // grid. Adding the reflow there is a no-op that reads like a fix; if an xterm upgrade
  // ever makes the font path stop self-repairing, this fails and the call belongs back.
  await page.goto('/renderer-harness.html')
  const result = await page.evaluate(() => window.runLetterboxExitRepair())

  expect(result.cols).toBeGreaterThan(0)
  expect(result.letterboxed).not.toEqual(result.base)
  expect(result.afterFontRestore).toEqual(result.base)
  expect(result.afterReflow).toEqual(result.afterFontRestore)
})
