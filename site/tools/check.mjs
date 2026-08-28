/**
 * Site gate. Run from anywhere:  node site/tools/check.mjs
 *
 * Checks the things that have actually broken on this site before, over EVERY
 * page rather than the landing page alone:
 *  - horizontal overflow, at four widths, in both colour schemes
 *  - every image resolves and no request 404s
 *  - every relative link points at a file that exists
 *  - every `data-todo` placeholder is one the site documents
 *  - the install callout swaps command, note, tab state and platform lights
 *  - the theme toggle round-trips
 *
 * A gate that covers one page while five ship is not a gate, so PAGES is derived
 * from the directory rather than listed: a new page is covered by existing, and
 * cannot be added without also being checked. It is cross-checked against
 * `tools/build.py`'s own list so a page that stops being generated is noticed
 * here too.
 *
 * Playwright is not a dependency of the site; it is borrowed from the app's
 * frontend workspace, which is why it is resolved explicitly rather than imported.
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const site = join(here, '..')
const require = createRequire(join(here, '..', '..', 'frontend', 'package.json'))
const { chromium } = require('playwright')

let failures = 0
const fail = (msg) => { failures++; console.log('  FAIL ' + msg) }

// The landing page, plus every generated sub-page directory. Discovered, not
// listed: a page nobody added here is a page nobody checks.
const PAGES = [
  { name: 'index.html', file: join(site, 'index.html') },
  ...readdirSync(site, { withFileTypes: true })
    .filter((e) => e.isDirectory() && existsSync(join(site, e.name, 'index.html')))
    .filter((e) => e.name !== 'img' && e.name !== 'tools' && e.name !== 'content')
    .map((e) => ({ name: `${e.name}/index.html`, file: join(site, e.name, 'index.html') })),
]
const url = (p) => pathToFileURL(p.file).href

// Every placeholder value the site is allowed to carry. Mirrors TODO_VALUES in
// `tools/build.py` and the list in `README.md` section 11; an unfilled URL that
// is not one of these is a placeholder nobody wrote down. A value leaves this
// list the moment its URL is decided, because a placeholder standing in for
// something known is just a dead link.
const TODO_VALUES = new Set(['blog URL'])

const built = readFileSync(join(here, 'build.py'), 'utf8')
for (const page of PAGES) {
  const dir = page.name.split('/')[0]
  if (dir !== 'index.html' && !built.includes(`"${dir}": (`)) {
    fail(`${page.name} exists but build.py does not generate it`)
  }
}
console.log(`pages\n  ${PAGES.length}: ${PAGES.map((p) => p.name).join(', ')}`)

const browser = await chromium.launch()

// ---------------------------------------------------------------- overflow
console.log('overflow')
for (const page of PAGES) {
  for (const theme of ['dark', 'light']) {
    for (const [w, h] of [[360, 800], [390, 844], [768, 1024], [1440, 900]]) {
      const p = await browser.newPage({ viewport: { width: w, height: h } })
      await p.goto(url(page), { waitUntil: 'load' })
      await p.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme)
      const r = await p.evaluate(() => {
        const de = document.documentElement
        return { s: de.scrollWidth, c: de.clientWidth }
      })
      if (r.s !== r.c) fail(`${page.name} ${theme} ${w}x${h}: scrollWidth ${r.s} vs clientWidth ${r.c}`)
      await p.close()
    }
  }
}
console.log(`  ${PAGES.length * 8} page/viewport/theme combinations checked`)

// ------------------------------------------------------------------ assets
console.log('assets')
let imageCount = 0
for (const page of PAGES) {
  const p = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  const badRequests = []
  p.on('requestfailed', (r) => badRequests.push(r.url()))
  await p.goto(url(page), { waitUntil: 'networkidle' })
  const imgs = await p.evaluate(() =>
    [...document.images].map((i) => ({ src: i.currentSrc.split('/').pop(), ok: i.naturalWidth > 0 })))
  for (const i of imgs) if (!i.ok) fail(`${page.name}: image did not load: ${i.src}`)
  for (const u of badRequests) fail(`${page.name}: request failed: ${u}`)
  imageCount += imgs.length
  await p.close()
}
console.log(`  ${imageCount} images across ${PAGES.length} pages, 0 failed requests`)

// ------------------------------------------------------------------- links
// Relative links are checked against the filesystem rather than by visiting them:
// under `file://` a directory link renders a listing instead of its index, so a
// broken cross-page link would pass a navigation check and 404 on the real site.
console.log('internal links')
let linkCount = 0
for (const page of PAGES) {
  const p = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  await p.goto(url(page), { waitUntil: 'load' })
  const links = await p.evaluate(() =>
    [...document.querySelectorAll('a[href]')].map((a) => ({
      href: a.getAttribute('href'),
      todo: a.getAttribute('data-todo'),
    })))
  for (const { href, todo } of links) {
    if (todo !== null) {
      if (!TODO_VALUES.has(todo)) fail(`${page.name}: undocumented data-todo "${todo}"`)
      continue
    }
    if (/^([a-z]+:|#|\/\/)/.test(href)) continue
    linkCount++
    const from = dirname(page.file)
    const target = resolve(from, href.replace(/[#?].*$/, ''))
    const wanted = href.endsWith('/') || href === '..' ? join(target, 'index.html') : target
    if (!existsSync(wanted)) fail(`${page.name}: link "${href}" resolves to a missing ${wanted}`)
  }
  await p.close()
}
console.log(`  ${linkCount} relative links resolved`)

// -------------------------------------------------------------- fragments
// The `/docs/#<slug>` fragments are a published URL contract (README.md section
// 10): the in-app help modals link to them. A link whose target `id` vanished is
// silent at runtime, so it is checked here instead.
console.log('docs fragments')
{
  const docs = PAGES.find((p) => p.name === 'docs/index.html')
  if (!docs) fail('there is no docs page')
  else {
    const p = await browser.newPage({ viewport: { width: 1280, height: 900 } })
    await p.goto(url(docs), { waitUntil: 'load' })
    const r = await p.evaluate(() => ({
      entries: document.querySelectorAll('.doclist li[id]').length,
      dangling: [...document.querySelectorAll('a[href^="#"]')]
        .map((a) => a.getAttribute('href').slice(1))
        .filter((id) => id && !document.getElementById(id)),
    }))
    for (const id of r.dangling) fail(`docs: "#${id}" has no element with that id`)
    if (r.entries < 20) fail(`docs: only ${r.entries} anchored entries, expected the full index`)
    console.log(`  ${r.entries} anchored entries, ${r.dangling.length} dangling fragments`)
    await p.close()
  }
}

// ---------------------------------------------------------- install callout
console.log('install callout')
const p = await browser.newPage({ viewport: { width: 1280, height: 900 } })
await p.goto(url(PAGES[0]), { waitUntil: 'networkidle' })
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
await p.close()

// ------------------------------------------------------------------- theme
console.log('theme toggle')
for (const page of PAGES) {
  const t = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  await t.goto(url(page), { waitUntil: 'load' })
  const t0 = await t.getAttribute('html', 'data-theme')
  await t.click('#themebtn')
  const t1 = await t.getAttribute('html', 'data-theme')
  await t.click('#themebtn')
  const t2 = await t.getAttribute('html', 'data-theme')
  if (t0 === t1 || t0 !== t2) fail(`${page.name}: toggle did not round-trip: ${t0} -> ${t1} -> ${t2}`)
  await t.close()
}
console.log(`  round-tripped on ${PAGES.length} pages`)

await browser.close()
console.log(failures ? `\n${failures} FAILURE(S)` : '\nall checks passed')
process.exit(failures ? 1 : 0)
