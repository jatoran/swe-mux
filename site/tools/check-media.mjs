/** Headless frontpage checks: focused, visible stills below the interactive demo. */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import os from 'node:os'
import { mkdir, readFile } from 'node:fs/promises'
import { chromium } from '../../frontend/node_modules/playwright/index.mjs'
import { serveSite } from '../../frontend/scripts/capture-demo.mjs'

try { os.setPriority(os.constants.priority.PRIORITY_BELOW_NORMAL) } catch {}
const manifest = JSON.parse(await readFile('site/img/frontpage/manifest.json', 'utf8'))
assert.equal(manifest.assets.length, 12)
for (const asset of manifest.assets) {
  assert.equal(asset.overlays, false, `${asset.name}: capture must exclude overlays`)
  assert.ok(asset.crop.width < asset.viewport.width || asset.crop.height < asset.viewport.height,
    `${asset.name}: capture must focus on a feature`)
  const bytes = await readFile(`site/img/frontpage/${asset.file}`)
  assert.equal(bytes.length, asset.bytes)
  assert.equal(createHash('sha256').update(bytes).digest('hex').slice(0, 12), asset.revision)
}

const { server, port } = await serveSite()
const origin = `http://127.0.0.1:${port}`
const browser = await chromium.launch({ headless: true })
try {
  await mkdir('.tmp/frontpage/review', { recursive: true })
  for (const width of [360, 390, 768, 1440]) {
    for (const theme of ['dark', 'light']) {
      const page = await browser.newPage({ viewport: { width, height: 900 } })
      const failures = []
      const mediaRequests = []
      page.on('pageerror', error => failures.push(String(error)))
      page.on('request', request => { if (/\.(mp4|webm|mp3)(\?|$)/.test(request.url())) mediaRequests.push(request.url()) })
      await page.goto(origin)
      await page.evaluate(theme => document.documentElement.setAttribute('data-theme', theme), theme)
      assert.equal(await page.locator('audio, video, [data-sound]').count(), 0, 'homepage has no playback media')
      assert.equal(await page.locator('.feature-shot img').count(), manifest.assets.length)
      assert.equal(await page.locator('details .feature-shot').count(), 0, 'feature images are immediately visible')
      for (const asset of manifest.assets) {
        const img = page.locator(`.feature-shot img[src^="img/frontpage/${asset.file}?"]`)
        await img.scrollIntoViewIfNeeded()
        await img.evaluate(node => node.decode())
        assert.deepEqual(await img.evaluate(node => [node.naturalWidth, node.naturalHeight]), [asset.width, asset.height])
        assert.ok(await img.getAttribute('alt'))
        assert.ok((await img.getAttribute('src')).endsWith(asset.revision))
        assert.equal(await img.locator('..').getAttribute('href'), await img.getAttribute('src'), 'full-size link matches the image')
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${width}px ${theme}: no overflow`)
      // These capture the delivered layout, not the capture rig's raw application frames.
      for (const section of ['fleet', 'mobile', 'voice', 'normalize', 'workbench', 'communication', 'git', 'history']) {
        await page.locator(`#${section}`).screenshot({ path: `.tmp/frontpage/review/${section}-${width}-${theme}.png` })
      }
      assert.deepEqual(mediaRequests, [], 'no video or audio downloads on the frontpage')
      assert.deepEqual(failures, [])
      await page.close()
      console.log(`${width}px ${theme}: 12 stills loaded, no playback media or overflow`)
    }
  }
  console.log(`${manifest.assets.reduce((total, asset) => total + asset.bytes, 0)} bytes of focused feature images`)
} finally {
  await browser.close()
  server.closeAllConnections()
  await new Promise(resolve => server.close(resolve))
}
