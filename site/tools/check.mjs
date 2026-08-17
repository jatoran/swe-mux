/**
 * Landing-page gate. Run from anywhere:  node site/tools/check.mjs
 *
 * Checks the things that have actually broken on this page before:
 *  - horizontal overflow, at four widths, in both colour schemes
 *  - every image resolves and no request 404s
 *  - the install callout swaps command, note, tab state and platform lights
 *  - the theme toggle round-trips
 *
 * Playwright is not a dependency of the site; it is borrowed from the app's
 * frontend workspace, which is why it is resolved explicitly rather than imported.
 */
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const require = createRequire(join(here, '..', '..', 'frontend', 'package.json'))
const { chromium } = require('playwright')

const url = pathToFileURL(join(here, '..', 'index.html')).href
const browser = await chromium.launch()
let failures = 0
const fail = (msg) => { failures++; console.log('  FAIL ' + msg) }

// ---------------------------------------------------------------- overflow
console.log('overflow')
for (const theme of ['dark', 'light']) {
  for (const [w, h] of [[360, 800], [390, 844], [768, 1024], [1440, 900]]) {
    const page = await browser.newPage({ viewport: { width: w, height: h } })
    await page.goto(url, { waitUntil: 'load' })
    await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme)
    const r = await page.evaluate(() => {
      const de = document.documentElement
      return { s: de.scrollWidth, c: de.clientWidth }
    })
    if (r.s !== r.c) fail(`${theme} ${w}x${h}: scrollWidth ${r.s} vs clientWidth ${r.c}`)
    await page.close()
  }
}
console.log('  8 viewport/theme combinations checked')

// ------------------------------------------------------------------ assets
const p = await browser.newPage({ viewport: { width: 1280, height: 900 } })
const badRequests = []
p.on('requestfailed', (r) => badRequests.push(r.url()))
await p.goto(url, { waitUntil: 'networkidle' })

console.log('assets')
const imgs = await p.evaluate(() =>
  [...document.images].map((i) => ({ src: i.currentSrc.split('/').pop(), ok: i.naturalWidth > 0 })))
for (const i of imgs) if (!i.ok) fail(`image did not load: ${i.src}`)
for (const u of badRequests) fail(`request failed: ${u}`)
console.log(`  ${imgs.length} images, ${badRequests.length} failed requests`)

// ---------------------------------------------------------- install callout
console.log('install callout')
const EXPECT = {
  powershell: ['windows'],
  curl: ['linux'],
  uv: ['windows', 'linux'],
  source: ['windows', 'linux'],
}
let lastCmd = null
for (const [method, os] of Object.entries(EXPECT)) {
  await p.click(`.ic-tab[data-m="${method}"]`)
  const r = await p.evaluate(() => ({
    cmd: document.getElementById('iccmd').textContent,
    note: document.getElementById('icnote').textContent.trim(),
    on: [...document.querySelectorAll('.ic-os span.on')].map((s) => s.dataset.os),
    sel: [...document.querySelectorAll('.ic-tab[aria-selected="true"]')].map((t) => t.dataset.m),
  }))
  if (r.sel.length !== 1 || r.sel[0] !== method) fail(`${method}: selected tab is ${r.sel}`)
  if (r.on.join() !== os.join()) fail(`${method}: platforms lit ${r.on}, expected ${os}`)
  if (!r.cmd || r.cmd === lastCmd) fail(`${method}: command did not change`)
  if (!r.note) fail(`${method}: note is empty`)
  lastCmd = r.cmd
}
// macOS is deliberately never lit while the port is unverified; see README.md.
const macosLit = await p.evaluate(() => !!document.querySelector('.ic-os span[data-os="macos"].on'))
if (macosLit) fail('macos is lit, but the macOS port is not verified yet')
console.log(`  ${Object.keys(EXPECT).length} methods checked, macos correctly unlit`)

// ------------------------------------------------------------------- theme
console.log('theme toggle')
const t0 = await p.getAttribute('html', 'data-theme')
await p.click('#themebtn')
const t1 = await p.getAttribute('html', 'data-theme')
await p.click('#themebtn')
const t2 = await p.getAttribute('html', 'data-theme')
if (t0 === t1 || t0 !== t2) fail(`toggle did not round-trip: ${t0} -> ${t1} -> ${t2}`)
console.log(`  ${t0} -> ${t1} -> ${t2}`)

await browser.close()
console.log(failures ? `\n${failures} FAILURE(S)` : '\nall checks passed')
process.exit(failures ? 1 : 0)
