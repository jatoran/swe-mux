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
  const changedPixelRatio = await page.evaluate(async ({ firstPng, secondPng }) => {
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
    for (let index = 0; index < firstPixels.length; index += 4) {
      if (Math.abs(firstPixels[index] - secondPixels[index]) > 4 ||
          Math.abs(firstPixels[index + 1] - secondPixels[index + 1]) > 4 ||
          Math.abs(firstPixels[index + 2] - secondPixels[index + 2]) > 4 ||
          Math.abs(firstPixels[index + 3] - secondPixels[index + 3]) > 4) changed += 1
    }
    return changed / (firstPixels.length / 4)
  }, { firstPng: first.toString('base64'), secondPng: second.toString('base64') })

  // SwiftShader can vary a small number of antialiased edge pixels between captures.
  // Stale rows alter a substantial portion of the viewport, so keep a narrow 0.5% ceiling.
  if (changedPixelRatio >= 0.005) {
    await testInfo.attach('first-terminal-frame', { body: first, contentType: 'image/png' })
    await testInfo.attach('second-terminal-frame', { body: second, contentType: 'image/png' })
  }
  expect(changedPixelRatio, 'terminal pixels should remain stable after the render queue drains').toBeLessThan(0.005)
  expect(rendererErrors.filter(error => /WebglRenderer|_updateModel|addon-webgl/i.test(error))).toEqual([])
})
