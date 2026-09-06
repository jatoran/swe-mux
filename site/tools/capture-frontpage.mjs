/** Capture only the feature being described, from the shipped demo, headlessly.
 * Run from the repository root: node site/tools/capture-frontpage.mjs [image-name]
 * Requires frontend dependencies, Playwright Chromium and ffmpeg on PATH.
 * Existing output is moved to .trash before replacement. No app daemon is started.
 */
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { chromium } from '../../frontend/node_modules/playwright/index.mjs'
import { serveSite } from '../../frontend/scripts/capture-demo.mjs'

const overlays = '.demo-director, .demo-show, .demo-bar, .tutorial-layer, .tutorial-overlay'
const command = (page, detail) => page.evaluate(detail => window.dispatchEvent(new CustomEvent('mux:command', { detail })), detail)
const queueArea = async page => {
  const top = await page.locator('.drawer-body-queue').boundingBox()
  const bottom = await page.locator('.queue-item').last().boundingBox()
  return { x: top.x, y: top.y, width: top.width, height: bottom.y + bottom.height - top.y + 10 }
}
const jobs = [
  { name: 'fleet', scenario: 'status', beat: 1, viewport: [1400, 850],
    ready: '[data-sidebar-session-id="s-migrate"]',
    area: async page => {
      const first = await page.locator('[data-sidebar-session-id="s-claude"]').boundingBox()
      const last = await page.locator('[data-sidebar-session-id="s-migrate"]').boundingBox()
      return { x: 0, y: first.y - 27, width: 340, height: last.y + last.height - first.y + 35 }
    } },
  { name: 'alert', scenario: 'status', beat: 2,
    prepare: async page => {
      await command(page, 'drawer.show:notifications')
      await page.getByRole('tab', { name: 'History', exact: true }).click()
    },
    ready: '.notification-list article',
    area: page => page.locator('.notification-list article').filter({ hasText: 'A decision is waiting' }).boundingBox() },
  { name: 'voice', scenario: 'voice', beat: 5, ready: '.assistant-action', area: async page => {
    const box = await page.locator('.assistant-panel').boundingBox()
    return { x: box.x, y: 4, width: box.width, height: box.y + box.height - 4 }
  } },
  { name: 'input', scenario: 'input', beat: 2, ready: '.terminal-pane.focused .xterm-screen',
    area: async page => {
      const box = await page.locator('.terminal-pane.focused .xterm-screen').boundingBox()
      return { x: box.x, y: box.y + box.height - 67, width: 330, height: 67 }
    } },
  { name: 'attachment', scenario: 'attachment', beat: 3, ready: '.terminal-pane.focused .xterm-screen',
    prepare: async page => {
      await page.waitForFunction(() => document.querySelector('.terminal-pane.focused')?.textContent?.includes('[image: cart.png]'))
    },
    area: async page => {
      const box = await page.locator('.terminal-pane.focused .xterm-screen').boundingBox()
      return { x: box.x, y: box.y + box.height - 67, width: 330, height: 67 }
    } },
  { name: 'clipboard', scenario: 'clipboard', beat: 1,
    prepare: async page => {
      await page.getByText('ALL CLIPBOARD HISTORY', { exact: false }).click()
      await page.locator('.clipboard-entry-body').first().click()
      await page.locator('.clipboard-entry-text').filter({ hasText: 'npm test' }).waitFor()
    },
    ready: '.clipboard-entry.expanded',
    area: async page => {
      const top = await page.locator('.clipboard-search').boundingBox()
      const bottom = await page.locator('.clipboard-entries').boundingBox()
      return { x: top.x, y: top.y - 28, width: top.width, height: bottom.y + bottom.height - top.y + 28 }
    } },
  { name: 'panes', scenario: 'preview', beat: 5, viewport: [1440, 420], ready: '.preview-frame',
    prepare: async page => {
      await page.locator('.preview-frame').waitFor()
      await page.frameLocator('.preview-frame iframe').getByRole('heading', { name: 'Checkout preview' }).waitFor()
    },
    area: '.main-stage' },
  { name: 'communication', scenario: 'orchestrate', beat: 9,
    ready: '.queue-item',
    area: queueArea },
  { name: 'land', scenario: 'land', beat: 4,
    prepare: async page => {
      await page.locator('.git-land-pipeline-step.run').filter({ hasText: 'Verifying' }).waitFor()
      await page.locator('.git-landing-summary').click()
    },
    ready: '.git-landing', area: '.git-landing' },
  { name: 'landfailure', scenario: 'landfailure', beat: 6,
    prepare: async page => { await page.locator('[data-sidebar-session-id="s-working"]').click() },
    ready: '.queue-item', area: queueArea },
  { name: 'history', scenario: 'history', beat: 2,
    prepare: async page => {
      const search = page.getByPlaceholder('Search transcript')
      await search.fill('test')
    },
    ready: '.transcript-tab-body mark', area: async page => {
      const top = await page.locator('.drawer-body-transcript').boundingBox()
      const bottom = await page.locator('.transcript-tab-body article').last().boundingBox()
      return { x: top.x, y: top.y, width: top.width, height: bottom.y + bottom.height - top.y + 8 }
    } },
  { name: 'phone', scenario: 'status', beat: 3, viewport: [390, 640], mobile: true,
    prepare: async page => { await page.locator('.notification-toast').waitFor({ state: 'hidden' }) },
    ready: '.terminal-pane.focused .xterm-screen',
    area: async page => {
      const box = await page.locator('.terminal-pane.focused .xterm-screen').boundingBox()
      return { x: 0, y: box.y + box.height - 224, width: 390, height: 640 - (box.y + box.height - 224) }
    } },
]

const output = resolve('site/img/frontpage')
const archive = resolve('.trash/frontpage-images', String(Date.now()))
await mkdir(output, { recursive: true })
await mkdir(archive, { recursive: true })
await mkdir('.tmp/frontpage/review', { recursive: true })
let assets = []
try { assets = JSON.parse(await readFile(`${output}/manifest.json`, 'utf8')).assets } catch (error) { if (error.code !== 'ENOENT') throw error }
const selected = process.argv.slice(2)
assert.ok(selected.every(name => jobs.some(job => job.name === name)), 'unknown image name')
const { server, port } = await serveSite()
const browser = await chromium.launch({ headless: true })
try {
  for (const job of jobs.filter(job => !selected.length || selected.includes(job.name))) {
    const [width, height] = job.viewport ?? [1100, 620]
    const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 2, isMobile: !!job.mobile, hasTouch: !!job.mobile })
    try {
      await context.addInitScript(() => {
        if (window.top !== window) return
        localStorage.setItem('mux.drawer.width.v1', '700')
        localStorage.setItem('mux.sidebar.width.v1', '340')
        window.__captureOverlaySeen = false
        new MutationObserver(() => {
          if (document.querySelector('.demo-director, .demo-show, .demo-bar, .tutorial-layer, .tutorial-overlay')) window.__captureOverlaySeen = true
        }).observe(document, { childList: true, subtree: true })
      })
      const page = await context.newPage()
      const errors = []
      page.on('pageerror', error => errors.push(String(error)))
      await page.goto(`http://127.0.0.1:${port}/demo/?capture=1&deterministic=1&scenario=${job.scenario}`)
      await page.waitForFunction(() => window.__demoDirector?.snapshot().running)
      await page.evaluate(() => window.__demoDirector.togglePlayback())
      while (await page.evaluate(() => window.__demoDirector.snapshot().index) < job.beat) {
        await page.waitForFunction(() => !window.__demoDirector.snapshot().acting)
        if (job.name === 'attachment' && await page.evaluate(() => window.__demoDirector.snapshot().index) === 1) {
          await page.locator('.terminal-pane.focused').getByText('review checkout flow', { exact: true }).waitFor()
          await page.locator('.terminal-pane.focused .xterm-helper-textarea').focus()
        }
        const previous = await page.evaluate(() => { const director = window.__demoDirector; const index = director.snapshot().index; director.advance(); return index })
        await page.waitForFunction(index => window.__demoDirector.snapshot().index > index, previous)
        if (job.name === 'attachment' && previous === 1) {
          await page.locator('.terminal-clip-toast').filter({ hasText: '1 file attached' }).waitFor()
          await page.waitForFunction(() => document.querySelector('.terminal-pane.focused')?.textContent?.includes('[image: cart.png]'))
        }
      }
      await page.waitForFunction(() => !window.__demoDirector.snapshot().acting)
      // Fixture mutations refresh the app asynchronously. Wait for the subject itself.
      await page.evaluate(() => window.__demoDirector.stop('dismissed'))
      if (job.prepare) await job.prepare(page)
      await page.locator(job.ready).first().waitFor()
      assert.equal(await page.locator(overlays).count(), 0)
      assert.equal(await page.evaluate(() => window.__captureOverlaySeen), false)
      await page.evaluate(() => document.fonts.ready)
      const raw = typeof job.area === 'string' ? await page.locator(job.area).boundingBox() : await job.area(page)
      assert.ok(raw && raw.width > 0 && raw.height > 0, `${job.name}: missing crop subject`)
      const clip = { x: Math.floor(raw.x), y: Math.floor(raw.y), width: Math.ceil(raw.width), height: Math.ceil(raw.height) }
      assert.ok(clip.width < width || clip.height < height, `${job.name}: must be a feature crop`)
      const png = await page.screenshot({ clip, animations: 'disabled', caret: 'hide', path: `.tmp/frontpage/review/${job.name}.png` })
      assert.deepEqual(errors, [], `${job.name}: browser errors`)
      const webp = execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-i', 'pipe:0', '-c:v', 'libwebp', '-quality', '92', '-f', 'webp', 'pipe:1'], { input: png, maxBuffer: 8 * 1024 * 1024, windowsHide: true })
      const file = `${job.name}.webp`
      try { await rename(`${output}/${file}`, `${archive}/${file}`) } catch (error) { if (error.code !== 'ENOENT') throw error }
      await writeFile(`${output}/${file}`, webp)
      assets = assets.filter(asset => asset.name !== job.name)
      assets.push({ name: job.name, file, scenario: job.scenario, beat: job.beat, viewport: { width, height }, crop: clip, width: clip.width * 2, height: clip.height * 2, bytes: webp.length, revision: createHash('sha256').update(webp).digest('hex').slice(0, 12), overlays: false })
      await writeFile(`${output}/manifest.json`, JSON.stringify({ version: 1, assets }, null, 2) + '\n')
      console.log(`${job.name}: ${clip.width}x${clip.height} crop, ${webp.length} bytes`)
    } finally { await context.close() }
  }
  // Keep cache revisions and intrinsic dimensions in sync when recapturing.
  const homePath = resolve('site/index.html')
  let home = await readFile(homePath, 'utf8')
  for (const asset of assets) {
    home = home.replace(new RegExp(`img/frontpage/${asset.name}\\.webp\\?v=[a-f0-9]+`, 'g'), `img/frontpage/${asset.file}?v=${asset.revision}`)
    const tag = new RegExp(`<img src="img/frontpage/${asset.name}\\.webp[^\"]*" width="\\d+" height="\\d+"`, 'g')
    home = home.replace(tag, `<img src="img/frontpage/${asset.file}?v=${asset.revision}" width="${asset.width}" height="${asset.height}"`)
  }
  await writeFile(homePath, home)
} finally {
  await browser.close()
  server.closeAllConnections()
  await new Promise(resolve => server.close(resolve))
}
