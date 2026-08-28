/**
 * Site gate. Run from anywhere:  node site/tools/check.mjs
 *
 * Checks the things that have actually broken on this site before, over EVERY
 * page rather than the landing page alone:
 *  - horizontal overflow, at four widths, in both colour schemes
 *  - every image resolves and no request 404s
 *  - every relative link points at a file that exists
 *  - every `data-todo` placeholder is one the site documents
 *  - the docs browser: its sidebar, its search, its prev/next chain, and that
 *    no page in it links back into `.docs/`
 *  - the install callout swaps command, note, tab state and platform lights
 *  - the theme toggle round-trips
 *
 * A gate that covers one page while five ship is not a gate, so PAGES is derived
 * from the directory rather than listed: a new page is covered by existing, and
 * cannot be added without also being checked. It is cross-checked against
 * `tools/build.py`'s own list so a page that stops being generated is noticed
 * here too - and, for the docs sub-pages, against the generated search index,
 * which is the registry those are declared from.
 *
 * Playwright is not a dependency of the site; it is borrowed from the app's
 * frontend workspace, which is why it is resolved explicitly rather than imported.
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const site = join(here, '..')
const require = createRequire(join(here, '..', '..', 'frontend', 'package.json'))
const { chromium } = require('playwright')

let failures = 0
const fail = (msg) => { failures++; console.log('  FAIL ' + msg) }

// The landing page, plus every generated sub-page directory. Discovered, not
// listed: a page nobody added here is a page nobody checks.
//
// It walks TWO levels rather than one, because `/docs/` is a documentation
// browser and its pages live at `docs/<slug>/index.html`. A one-level walk would
// have checked the docs index and silently skipped every page under it, which is
// the whole of the documentation.
const SKIP = new Set(['img', 'tools', 'content'])
function discover(dir, prefix, depth) {
  const found = []
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (!e.isDirectory() || SKIP.has(e.name)) continue
    const here = join(dir, e.name)
    const name = `${prefix}${e.name}/index.html`
    if (existsSync(join(here, 'index.html'))) found.push({ name, file: join(here, 'index.html') })
    if (depth > 1) found.push(...discover(here, `${prefix}${e.name}/`, depth - 1))
  }
  return found
}
const PAGES = [
  { name: 'index.html', file: join(site, 'index.html') },
  ...discover(site, '', 2),
]
const url = (p) => pathToFileURL(p.file).href
const DOCS = PAGES.filter((p) => p.name.startsWith('docs/'))

// Every placeholder value the site is allowed to carry. Mirrors TODO_VALUES in
// `tools/build.py` and the list in `README.md` section 11; an unfilled URL that
// is not one of these is a placeholder nobody wrote down. A value leaves this
// list the moment its URL is decided, because a placeholder standing in for
// something known is just a dead link.
// Empty, and kept: the last entry left when the blog got a decided address.
// See TODO_VALUES in `tools/build.py` for why an undecided URL and an unwritten
// page are tracked by two different mechanisms.
const TODO_VALUES = new Set([])

// Pages the chrome links to that another branch owns and has not landed yet.
// This is not a second placeholder mechanism: a `data-todo` link points at `#`
// and is a URL nobody has decided, while these are decided URLs whose file
// arrives with the branch that writes the page. Recording them is what lets the
// two branches land in either order without a red gate in between.
//
// It is a debt list, so it is checked in both directions. An entry whose file
// now exists is checked like any other link, and an entry that no page links to
// is a line nobody removed - that one fails, because a permanent exemption is
// how a broken link becomes permanent too.
// Empty, and kept: the last three entries (`blog/`, `privacy/`, `terms/`) left
// when their pages landed, the same posture as TODO_VALUES above.
const PENDING_PAGES = new Set([])
const pendingSeen = new Set()

// Each top-level page directory has to be one `build.py` declares, so a
// directory holding an `index.html` nothing generates cannot ship. The docs
// sub-pages are registered from `docs_content.py` rather than written out here,
// so they are cross-checked against the generated search index instead - see the
// `docs browser` section, which is a stronger check than this substring one.
const built = readFileSync(join(here, 'build.py'), 'utf8')
for (const page of PAGES) {
  const parts = page.name.split('/')
  if (parts.length !== 2) continue
  if (!built.includes(`"${parts[0]}": (`)) {
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
    // Strip the fragment BEFORE the trailing-slash test, not after: `../#install`
    // is a link to a directory and would otherwise be resolved as a file named
    // `install`, which happens to exist as a directory and so passes for the
    // wrong reason.
    const path = href.replace(/[#?].*$/, '')
    if (!path) continue
    linkCount++
    const from = dirname(page.file)
    const target = resolve(from, path)
    const wanted = path.endsWith('/') || path === '..' ? join(target, 'index.html') : target
    if (existsSync(wanted)) continue
    const rel = relative(site, wanted).split(sep).join('/')
    if (PENDING_PAGES.has(rel)) pendingSeen.add(rel)
    else fail(`${page.name}: link "${href}" resolves to a missing ${wanted}`)
  }
  await p.close()
}
console.log(`  ${linkCount} relative links resolved`)
for (const rel of PENDING_PAGES) {
  if (!existsSync(join(site, rel))) {
    if (pendingSeen.has(rel)) console.log(`  pending: ${rel} is linked and not written yet`)
    else fail(`PENDING_PAGES lists ${rel}, which no page links to; remove the entry`)
  }
}

// ----------------------------------------------------------- docs browser
// `/docs/` is a documentation browser rather than one anchored page: a
// persistent sidebar, one URL per topic, search, and prev/next.
//
// This replaced a `docs fragments` section that asserted `.doclist li[id] >= 20`
// and no dangling fragments. Those assumptions died with the index it was
// written against, and the rule that made them worth having did not: a
// navigation surface whose targets silently stopped existing is invisible at
// runtime and obvious here. So each of its questions has a successor, and the
// two the restructure actually created are asserted too.
//
//  - every sidebar link resolves to a page that exists      (was: no dangling #)
//  - the sidebar is IDENTICAL on every docs page            (new)
//  - every docs page is in the search index, and vice versa (was: >= 20 entries)
//  - no docs page links to a `.docs/**.md` blob             (new - the point of
//    the whole package, and the regression most likely to creep back in one
//    "just this one reference" link at a time)
//  - prev/next forms one chain over that same order         (new)
//  - search finds a known string and lands on a real page   (new)
console.log('docs browser')
{
  const index = DOCS.find((p) => p.name === 'docs/index.html')
  if (!index) fail('there is no docs index')
  else if (DOCS.length < 10) fail(`only ${DOCS.length} docs pages; expected the browser`)

  // The sidebar's hrefs are relative, so they necessarily read differently from
  // `/docs/` and from `/docs/<slug>/`. What has to be identical is where they
  // LAND, so every comparison here is over the resolved target rather than over
  // the attribute - comparing the attribute would have been a check that could
  // only ever pass by accident.
  const target = (page, href) =>
    relative(site, resolve(dirname(page.file), href.replace(/[#?].*$/, ''), 'index.html'))
      .split(sep)
      .join('/')

  // The sidebar's own order, read off the index page once. Every later
  // assertion is made against this, so a page missing from it fails everywhere
  // rather than being quietly excused.
  const p0 = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  await p0.goto(url(index), { waitUntil: 'load' })
  const order = (
    await p0.evaluate(() =>
      [...document.querySelectorAll('.dsnav a')].map((a) => a.getAttribute('href')))
  ).map((href) => target(index, href))
  await p0.close()
  if (order.length !== DOCS.length) {
    fail(`the sidebar lists ${order.length} pages but ${DOCS.length} exist on disk`)
  }
  for (const name of order) {
    if (!existsSync(join(site, name))) fail(`the sidebar links to ${name}, which does not exist`)
  }
  for (const page of DOCS) {
    if (!order.includes(page.name)) fail(`${page.name} exists but no sidebar link reaches it`)
  }

  // The generated search index, read as a file rather than in a browser: it is
  // the registry the docs sub-pages are declared from, so it stands in for the
  // `build.py` substring check the top-level pages get.
  const indexFile = join(site, 'docs', 'search-index.js')
  let indexed = []
  if (!existsSync(indexFile)) fail('docs/search-index.js is missing; run site/tools/build.py')
  else {
    const src = readFileSync(indexFile, 'utf8')
    const body = src.slice(src.indexOf('window.__MUXDOCS =') + 18).replace(/;\s*$/, '')
    indexed = JSON.parse(body)
    const onDisk = DOCS.filter((p) => p.name !== 'docs/index.html')
      .map((p) => p.name.replace(/^docs\//, '').replace(/index\.html$/, ''))
    const inIndex = indexed.map((r) => r.u)
    for (const u of onDisk) if (!inIndex.includes(u)) fail(`docs/${u} is not in the search index`)
    for (const u of inIndex) if (!onDisk.includes(u)) fail(`search index carries docs/${u}, which does not exist`)
  }

  let steps = 0
  let checkedLinks = 0
  for (const page of DOCS) {
    const p = await browser.newPage({ viewport: { width: 1280, height: 900 } })
    await p.goto(url(page), { waitUntil: 'load' })
    const r = await p.evaluate(() => ({
      nav: [...document.querySelectorAll('.dsnav a')].map((a) => a.getAttribute('href')),
      current: [...document.querySelectorAll('.dsnav a[aria-current="page"]')].length,
      // Every link on the page, so the `.docs/` ban covers prose as well as
      // chrome. It is the ban that has to hold everywhere, not on the parts
      // somebody remembered.
      links: [...document.querySelectorAll('a[href]')].map((a) => a.getAttribute('href')),
      dangling: [...document.querySelectorAll('a[href^="#"]')]
        .map((a) => a.getAttribute('href').slice(1))
        .filter((id) => id && !document.getElementById(id)),
      prev: document.querySelector('.dsstep a[rel="prev"]')?.getAttribute('href') ?? null,
      next: document.querySelector('.dsstep a[rel="next"]')?.getAttribute('href') ?? null,
      search: !!document.getElementById('dsq'),
    }))
    await p.close()

    if (!r.search) fail(`${page.name}: no search control`)
    if (r.current !== 1) fail(`${page.name}: ${r.current} sidebar links marked aria-current`)
    for (const id of r.dangling) fail(`${page.name}: "#${id}" has no element with that id`)
    checkedLinks += r.links.length
    for (const href of r.links) {
      if (/\.docs\//.test(href)) {
        fail(`${page.name}: links into .docs/ ("${href}"); write the page instead`)
      }
    }
    const nav = r.nav.map((href) => target(page, href))
    if (nav.join('|') !== order.join('|')) {
      fail(`${page.name}: its sidebar reaches a different set of pages from the docs index's`)
    }

    // Prev and next, checked against the sidebar's own order. Deriving the
    // expectation from the nav rather than from a list here is what makes this
    // survive a page being inserted, and resolving both sides means a chain
    // that walks the right pages by the wrong route still passes, which is
    // correct - the route is not the contract.
    const at = order.indexOf(page.name)
    if (at > 0) {
      steps++
      const wantPrev = at === 1 ? null : order[at - 1]
      const wantNext = at === order.length - 1 ? null : order[at + 1]
      const gotPrev = r.prev === null ? null : target(page, r.prev)
      const gotNext = r.next === null ? null : target(page, r.next)
      if (gotPrev !== wantPrev) fail(`${page.name}: prev reaches ${gotPrev}, expected ${wantPrev}`)
      if (gotNext !== wantNext) fail(`${page.name}: next reaches ${gotNext}, expected ${wantNext}`)
    } else if (r.prev || r.next) {
      fail(`${page.name}: the docs index carries a prev/next step and should not`)
    }
  }

  // Search, driven the way a reader drives it. A prebuilt index is only useful
  // if the script over it actually resolves, ranks, and links - and none of
  // that is visible from reading the JSON.
  const from = DOCS.find((p) => p.name === 'docs/install/index.html') ?? index
  const s = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  await s.goto(url(from), { waitUntil: 'load' })
  await s.fill('#dsq', 'tailscale')
  await s.waitForSelector('#dsr a', { timeout: 5000 }).catch(() => {})
  const hits = await s.evaluate(() => ({
    loaded: Array.isArray(window.__MUXDOCS),
    links: [...document.querySelectorAll('#dsr a')].map((a) => a.getAttribute('href')),
    status: document.getElementById('dsstatus')?.textContent ?? '',
  }))
  if (!hits.loaded) fail('search: the index script did not load')
  if (!hits.links.length) fail('search: "tailscale" returned nothing')
  // The phone page is where Tailscale is explained, so it has to be in there.
  if (!hits.links.some((h) => h.startsWith('../phone/'))) {
    fail(`search: "tailscale" did not return the phone page (got ${hits.links})`)
  }
  for (const href of hits.links) {
    if (!existsSync(join(site, target(from, href)))) {
      fail(`search: result "${href}" resolves to a missing page`)
    }
  }
  // `/` focuses the box, which is the shortcut a reader tries first.
  await s.evaluate(() => document.getElementById('dsq').blur())
  await s.keyboard.press('/')
  const focused = await s.evaluate(() => document.activeElement === document.getElementById('dsq'))
  if (!focused) fail('search: "/" did not focus the search box')
  // Arrow-down out of the box has to land on a result, or the list is
  // mouse-only.
  await s.keyboard.press('ArrowDown')
  const onResult = await s.evaluate(() => document.activeElement?.closest('#dsr') !== null)
  if (!onResult) fail('search: ArrowDown did not move focus into the results')
  await s.close()

  // Phrased as what was inspected rather than as a verdict, because `fail()`
  // only counts: a summary claiming "no links into .docs/" would print directly
  // under the FAIL saying otherwise.
  console.log(
    `  ${DOCS.length} pages: ${order.length} sidebar targets compared on each, ` +
      `${indexed.length} indexed, ${steps} prev/next steps, ` +
      `${checkedLinks} hrefs read for .docs/ blobs, ` +
      `search returned ${hits.links.length} results for "tailscale" (${hits.status})`,
  )
}

// ---------------------------------------------------------- install callout
console.log('install callout')
const p = await browser.newPage({ viewport: { width: 1280, height: 900 } })
await p.goto(url(PAGES[0]), { waitUntil: 'networkidle' })
// swe-mux is on PyPI as a pure-Python `py3-none-any` wheel, so every method
// works on every host and the row is lit the same way throughout. That makes the
// per-method set a weak assertion on its own, which is why the two below it are
// the ones carrying weight now.
const EXPECT = {
  uv: ['windows', 'linux', 'macos'],
  pipx: ['windows', 'linux', 'macos'],
  pip: ['windows', 'linux', 'macos'],
  source: ['windows', 'linux', 'macos'],
}

// Hosts an install command is allowed to name. The callout shipped
// `get.swe-mux.dev` one-liners for a domain that was never registered - note the
// hyphen, which the real `swemux.dev` does not have - and no gate noticed,
// because nothing here had ever looked at what the command actually fetched.
// A one-liner that 404s is the worst thing this box can contain, so the rule is
// an allowlist rather than a spell-check: a new install host has to be added
// here deliberately, by someone who knows it resolves.
const INSTALL_HOSTS = new Set(['github.com'])

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
  for (const url of r.cmd.match(/https?:\/\/[^\s|'"]+/g) ?? []) {
    const host = new URL(url).host
    if (!INSTALL_HOSTS.has(host)) {
      fail(`${method}: command fetches from "${host}", which is not a host this project publishes`)
    }
  }
  lastCmd = r.cmd
}

// macOS is lit on every method, because the wheel installs there and CI proves
// it. It carries a qualifier anyway, because its CI leg is still
// `continue-on-error` and no CI job on any host starts a daemon. Both halves are
// asserted: dropping the light would understate a platform that works, and
// dropping the marker would overstate one that is not required to pass.
const macos = await p.evaluate(() => {
  const el = document.querySelector('.ic-os span[data-os="macos"]')
  return el && { lit: el.classList.contains('on'), qualifier: el.querySelector('em')?.textContent }
})
if (!macos) fail('the macos platform indicator is gone from the install callout')
else {
  if (!macos.lit) fail('macos is unlit, but the wheel installs there and CI smokes it')
  if (!macos.qualifier) fail('macos is lit with no qualifier, but its CI leg is still unproven')
}
// Phrased as what was inspected rather than as a verdict: `fail()` only counts,
// it does not stop, so a summary that claimed "no foreign install hosts" would
// print directly under the FAIL saying otherwise.
console.log(
  `  ${Object.keys(EXPECT).length} methods, their install hosts, and the macos ` +
    `indicator (lit, marked "${macos?.qualifier}") inspected`,
)
await p.close()

// ------------------------------------------------------------ bar and menu
// The header and footer exist in two places - `tools/build.py`'s `shell()` for
// the generated pages, and `index.html`'s hand-written copy for the landing page
// - and if they stop agreeing the site reads as two sites. Discipline is not
// what keeps them together; this is. Everything here runs on EVERY page, so a
// change made to one copy and not the other fails rather than ships.
console.log('bar and menu')
{
  // `install` is a call to action rather than a nav entry, and is the one
  // fragment link the chrome is allowed. The eight section anchors the landing
  // page's bar used to carry are what this assertion exists to keep out: an
  // anchor is a position in a document rather than a destination, and a bar
  // holding both means two things at once.
  //
  // `(\.\.\/)*` rather than `?` because a documentation page is two directories
  // below the deploy root and reaches the landing page's install callout with
  // `../../#install`. The depth is a property of the page, not a second kind of
  // link, and the assertion is about the fragment either way.
  const ALLOWED_FRAGMENT = /^(\.\.\/)*#install$/
  let labels = null
  for (const page of PAGES) {
    const b = await browser.newPage({ viewport: { width: 390, height: 844 } })
    await b.goto(url(page), { waitUntil: 'load' })

    const shape = await b.evaluate(() => {
      const chrome = [...document.querySelectorAll('.bar a[href], .menu a[href]')]
      return {
        fragments: chrome.map((a) => a.getAttribute('href')).filter((h) => h.includes('#')),
        menu: [...document.querySelectorAll('.menu a[href]')].map((a) => a.textContent.trim()),
        burger: !!document.getElementById('menubtn'),
        controls: document.getElementById('menubtn')?.getAttribute('aria-controls'),
        hidden: document.getElementById('menu')?.hasAttribute('hidden'),
        privacy: !!document.querySelector('footer a[href$="privacy/"]'),
        terms: !!document.querySelector('footer a[href$="terms/"]'),
        compare: !!document.querySelector('footer a[href$="compare/"]'),
        x: document.querySelector('footer .social a[href*="x.com"]')?.getAttribute('href'),
      }
    })
    for (const h of shape.fragments) {
      if (!ALLOWED_FRAGMENT.test(h)) fail(`${page.name}: in-page anchor "${h}" in the chrome`)
    }
    if (!shape.burger) fail(`${page.name}: no menu button`)
    if (shape.controls !== 'menu') fail(`${page.name}: menu button controls "${shape.controls}"`)
    if (shape.hidden !== true) fail(`${page.name}: the menu is not closed on load`)
    if (!shape.privacy || !shape.terms) fail(`${page.name}: the footer is missing privacy/terms`)
    if (!shape.compare) fail(`${page.name}: the footer is missing the compare link`)
    if (shape.x !== 'https://x.com/swemux') fail(`${page.name}: footer X link is "${shape.x}"`)
    if (labels === null) labels = shape.menu
    else if (shape.menu.join('|') !== labels.join('|')) {
      fail(`${page.name}: menu is [${shape.menu}], but ${PAGES[0].name} has [${labels}]`)
    }

    // Open by pointer, dismiss by keyboard, which is the pair that has to work:
    // a menu that opens and cannot be closed without a mouse is a trap on a
    // page whose whole nav is behind it.
    const mainTopBefore = await b.evaluate(
      () => document.querySelector('main').getBoundingClientRect().top)
    await b.click('#menubtn')
    // "It draws something" is not enough, and this is the shape that proved it:
    // `.wrap` is used by the menu panel and by the 44px bar row above it, and one
    // descendant selector made the panel a second bar row that laid its links
    // out sideways across the hero. The panel had a positive height throughout.
    // So the geometry is asserted: the panel starts below the row, every item is
    // inside the panel, and the items are stacked rather than in a line.
    const open = await b.evaluate(() => {
      const menu = document.getElementById('menu').getBoundingClientRect()
      const row = document.querySelector('.bar > .wrap').getBoundingClientRect()
      const items = [...document.querySelectorAll('.menu a[href]')].map((a) => a.getBoundingClientRect())
      return {
        expanded: document.getElementById('menubtn').getAttribute('aria-expanded'),
        focused: document.activeElement === document.querySelector('.menu a[href]'),
        overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        under: menu.top >= row.bottom - 1,
        escaped: items.filter((r) => r.top < menu.top - 1 || r.bottom > menu.bottom + 1).length,
        stacked: items.every((r, i) => i === 0 || r.top >= items[i - 1].bottom - 1),
        tall: Math.round(menu.height),
        count: items.length,
        // The panel is anchored to the edge its button sits at. A panel that
        // came back full-width or left-anchored would read as a mistake: the
        // control is top right, so the panel hangs from the right.
        rightGap: Math.round(document.documentElement.clientWidth - menu.right),
        mainTop: document.querySelector('main').getBoundingClientRect().top,
      }
    })
    if (open.expanded !== 'true') fail(`${page.name}: aria-expanded is ${open.expanded} when open`)
    if (!open.focused) fail(`${page.name}: opening did not move focus into the menu`)
    if (open.overflow) fail(`${page.name}: the open menu pushes the page sideways at 390`)
    if (!open.under) fail(`${page.name}: the open menu overlaps the bar row above it`)
    if (open.escaped) fail(`${page.name}: ${open.escaped} menu items fall outside the panel`)
    if (!open.stacked) fail(`${page.name}: the menu's items are not stacked in a column`)
    if (open.tall < open.count * 20) {
      fail(`${page.name}: the open menu is ${open.tall}px tall for ${open.count} items`)
    }
    if (Math.abs(open.rightGap) > 1) {
      fail(`${page.name}: the open menu sits ${open.rightGap}px off the right edge its button is at`)
    }
    // The panel overlays the page rather than reflowing it: opening must not
    // move the content underneath.
    if (open.mainTop !== mainTopBefore) {
      fail(`${page.name}: opening the menu shifted the page (main moved ` +
        `${mainTopBefore} -> ${open.mainTop})`)
    }

    await b.keyboard.press('Escape')
    const shut = await b.evaluate(() => ({
      expanded: document.getElementById('menubtn').getAttribute('aria-expanded'),
      hidden: document.getElementById('menu').hasAttribute('hidden'),
      // Escape that closes without returning focus drops a keyboard user back
      // at the top of the document, which is a worse place than they started.
      focused: document.activeElement === document.getElementById('menubtn'),
    }))
    if (shut.expanded !== 'false') fail(`${page.name}: aria-expanded is ${shut.expanded} when shut`)
    if (!shut.hidden) fail(`${page.name}: Escape did not close the menu`)
    if (!shut.focused) fail(`${page.name}: Escape did not return focus to the menu button`)
    await b.close()

    // Wide: the full page nav is drawn and the button is not, so the same links
    // are never offered twice in one bar.
    const w = await browser.newPage({ viewport: { width: 1440, height: 900 } })
    await w.goto(url(page), { waitUntil: 'load' })
    const wide = await w.evaluate(() => ({
      nav: [...document.querySelectorAll('.bar nav.pagenav a')]
        .filter((a) => a.offsetParent !== null).map((a) => a.textContent.trim()),
      burger: document.getElementById('menubtn').offsetParent !== null,
    }))
    if (wide.burger) fail(`${page.name}: the menu button is still drawn at 1440`)
    if (wide.nav.length !== labels.length - 2) {
      fail(`${page.name}: the wide nav is [${wide.nav}], expected the pages from [${labels}]`)
    }
    await w.close()
  }
  console.log(`  ${PAGES.length} pages: nav [${labels.join(' ')}], open/Escape/focus, footer links`)
}

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
