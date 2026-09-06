/** Headless checks for clean captures and user-controlled, seekable public videos. */
import assert from 'node:assert/strict'
import os from 'node:os'
import { mkdir, readFile } from 'node:fs/promises'
import { chromium } from '../../frontend/node_modules/playwright/index.mjs'
import { serveSite } from '../../frontend/scripts/capture-demo.mjs'

try { os.setPriority(os.constants.priority.PRIORITY_BELOW_NORMAL) } catch {}
const { server, port } = await serveSite()
const origin = `http://127.0.0.1:${port}`
const browser = await chromium.launch({ headless: true })
try {
  await mkdir('.tmp/site-media-refresh/review', { recursive: true })
  const capture = await browser.newPage({ viewport: { width: 1280, height: 720 } })
  await capture.goto(`${origin}/demo/?capture=1&deterministic=1&scenario=land`)
  await capture.waitForFunction(() => window.__demoDirector?.snapshot().index >= 2, null, { timeout: 30000 })
  assert.equal(await capture.locator('.demo-director, .demo-show, .demo-bar, .tutorial-layer, .tutorial-overlay').count(), 0)
  assert.ok(await capture.locator('.workspace').count(), 'capture must show the application')
  await capture.screenshot({ path: '.tmp/site-media-refresh/review/clean-capture.png' })
  await capture.close()

  for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } })
    const failures = []
    page.on('pageerror', error => failures.push(String(error)))
    await page.goto(origin)
    assert.equal(await page.locator('audio, [data-sound]').count(), 0, 'no audio samples')
    const videos = page.locator('video')
    assert.equal(await videos.count(), 11)
    for (let index = 0; index < await videos.count(); index++) {
      const video = videos.nth(index)
      await video.evaluate(node => { const details = node.closest('details'); if (details) details.open = true })
      await video.scrollIntoViewIfNeeded()
      await video.evaluate(node => node.load())
      await page.waitForFunction(node => node.readyState >= 1 && Number.isFinite(node.duration) && node.duration > 0,
        await video.elementHandle(), { timeout: 30000 })
      assert.equal(await video.evaluate(node => node.controls && !node.autoplay && !node.loop && node.paused), true)
      // Click the actual native play control. Tutorial buttons are not present in the recording.
      const box = await video.boundingBox()
      await video.click({ position: { x: 30, y: box.height - 28 } })
      await page.waitForFunction(node => !node.paused && node.currentTime > 0.05, await video.elementHandle())
      await video.click({ position: { x: 30, y: box.height - 28 } })
      await page.waitForFunction(node => node.paused, await video.elementHandle())
      const target = await video.evaluate(node => { const time = node.duration * .65; node.currentTime = time; return time })
      await page.waitForFunction(({ node, target }) => !node.seeking && Math.abs(node.currentTime - target) < .25,
        { node: await video.elementHandle(), target })
      // Negative quiet window: scrolling must not restart a video the visitor paused.
      await page.evaluate(() => window.scrollTo(0, 0))
      await video.scrollIntoViewIfNeeded()
      await page.waitForTimeout(200)
      assert.equal(await video.evaluate(node => node.paused), true)
      console.log(`${width}px video ${index + 1}: native play/pause and seek passed`)
    }
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: `.tmp/site-media-refresh/review/home-${width}.png` })
    assert.deepEqual(failures, [])
    await page.close()
  }

  const manifest = JSON.parse(await readFile('site/img/showcase-manifest.json', 'utf8'))
  for (const asset of manifest.assets) {
    assert.equal(asset.overlays, false, `${asset.name}: capture must exclude overlays`)
    for (const format of ['mp4', 'webm']) {
      const response = await fetch(`${origin}/img/showcase-${asset.name}.${format}`, { headers: { Range: 'bytes=0-15' } })
      assert.equal(response.status, 206)
      assert.equal((await response.arrayBuffer()).byteLength, 16)
      assert.match(response.headers.get('Content-Range'), /^bytes 0-15\/\d+$/)
    }
  }
  console.log('clean captures, audio removal, native controls and video ranges passed')
} finally {
  await browser.close()
  server.closeAllConnections()
  await new Promise(resolve => server.close(resolve))
}
