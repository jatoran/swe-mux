import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Page } from 'playwright/test'

// The preview runtime bridge (`src/swe_mux/assets/preview/runtime_bridge.js`), executed in
// a real Chromium - the exact bytes every proxied preview document receives, with its two
// placeholders filled the way `preview_transport._preview_runtime_bridge` fills them.
//
// On 2026-09-24 a registration for swe-mux's own origin put that origin in a Project's
// route table. Every same-origin URL in the Project's preview documents then looked like a
// "service", and the bridge re-prefixed each one on every mutation it caused - an
// unbounded microtask loop that froze the desktop app, whose renderer drew the preview.
// The phone was unaffected only because its page origin was a different host.

const BRIDGE = readFileSync(
  join(import.meta.dirname, '..', '..', '..', 'src', 'swe_mux', 'assets', 'preview', 'runtime_bridge.js'),
  'utf8',
)
// Not a live port: every request to this origin is fulfilled by the route below.
const ORIGIN = 'http://mux-bridge.test:8765'
const PREFIX = '/preview/abc/'
const SIBLING = 'http://127.0.0.1:37655'

function bridge(routes: Record<string, string>): string {
  return BRIDGE
    .replace('__MUX_PREVIEW_PREFIX__', JSON.stringify(PREFIX))
    .replace('__MUX_PROJECT_ROUTES__', JSON.stringify(routes))
}

async function open(page: Page, routes: Record<string, string>, body: string): Promise<void> {
  const html = `<!doctype html><html><head><script>${bridge(routes)}</script></head><body>${body}</body></html>`
  await page.route(`${ORIGIN}/**`, route =>
    route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html }))
  await page.goto(`${ORIGIN}${PREFIX}`, { waitUntil: 'load', timeout: 15_000 })
}

// Bounded, so a regression fails here instead of hanging the suite with the page.
async function responsive<T>(page: Page, work: () => T | Promise<T>): Promise<T> {
  return Promise.race([
    page.evaluate(work),
    new Promise<never>((_, reject) => setTimeout(() => reject(new Error('the page stopped responding')), 5_000)),
  ])
}

test("the page's own origin in the route table cannot start a rewrite loop", async ({ page }) => {
  const links = Array.from({ length: 2000 }, (_, index) => `<a href="dir-${index}/">dir-${index}/</a>`).join('')
  await open(page, { [ORIGIN]: '/preview/self/', [SIBLING]: '/preview/sibling/' }, links)

  const first = await responsive(page, () =>
    [...document.querySelectorAll('a')].slice(0, 3).map(a => a.getAttribute('href')))
  expect(first).toEqual(['dir-0/', 'dir-1/', 'dir-2/'])
  const count = await responsive(page, () => document.querySelectorAll('a[href^="dir-"]').length)
  expect(count).toBe(2000)
})

test('every rewrite happens exactly once', async ({ page }) => {
  await open(page, { [ORIGIN]: '/preview/self/', [SIBLING]: '/preview/sibling/' },
    `<a id="root" href="/docs">docs</a><a id="sibling" href="${SIBLING}/api">api</a>`)

  const settled = await responsive(page, async () => {
    const read = () => ({
      root: document.getElementById('root')!.getAttribute('href'),
      sibling: document.getElementById('sibling')!.getAttribute('href'),
    })
    await new Promise(resolve => setTimeout(resolve, 100))
    const before = read()
    const dynamic = document.createElement('a')
    document.body.appendChild(dynamic)
    dynamic.setAttribute('href', '/later')
    const once = dynamic.getAttribute('href')
    dynamic.setAttribute('href', once!)
    const again = dynamic.getAttribute('href')
    ;(dynamic as HTMLAnchorElement).href = `${'http://127.0.0.1:37655'}/y`
    const property = dynamic.getAttribute('href')
    await new Promise(resolve => setTimeout(resolve, 100))
    return { before, after: read(), once, again, property, final: dynamic.getAttribute('href') }
  })

  expect(settled.before.root).toBe(`${ORIGIN}${PREFIX}docs`)
  expect(settled.before.sibling).toBe(`${ORIGIN}/preview/sibling/api`)
  expect(settled.after).toEqual(settled.before)
  expect(settled.once).toBe(`${ORIGIN}${PREFIX}later`)
  expect(settled.again).toBe(settled.once)
  expect(settled.property).toBe(`${ORIGIN}/preview/sibling/y`)
  expect(settled.final).toBe(settled.property)
})

test('the page tells the pane where it is and what it loaded', async ({ page }) => {
  // The pane cannot read a sandboxed document's location, so refresh used to remount
  // the preview's root and live reload had nothing to watch. The bridge reports both.
  const document_ = `<!doctype html><html><head><script>${bridge({})}</script>`
    + `<link rel="stylesheet" href="/css/site.css"></head><body><img src="ref/a.png">`
    + `<img src="https://elsewhere.test/b.png"></body></html>`
  const host = `<!doctype html><html><body><script>window.__reports=[];`
    + `addEventListener('message',e=>window.__reports.push(e.data))</script>`
    + `<iframe src="${PREFIX}docs/page.html?v=1" sandbox="allow-scripts"></iframe></body></html>`
  await page.route('https://elsewhere.test/**', route => route.fulfill({ status: 200, body: '' }))
  await page.route(`${ORIGIN}/**`, route => {
    const url = new URL(route.request().url())
    if (url.pathname === '/host.html') return route.fulfill({ status: 200, contentType: 'text/html', body: host })
    if (url.pathname.endsWith('.html')) return route.fulfill({ status: 200, contentType: 'text/html', body: document_ })
    return route.fulfill({ status: 200, contentType: url.pathname.endsWith('.css') ? 'text/css' : 'image/png', body: '' })
  })
  await page.goto(`${ORIGIN}/host.html`, { waitUntil: 'load', timeout: 15_000 })
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as { __reports: { resources: string[] }[] }).__reports.some(item => item.resources.length > 0)),
  { timeout: 5_000 }).toBe(true)
  const reports = await page.evaluate(() => (window as unknown as { __reports: unknown[] }).__reports)
  expect(reports[0]).toEqual({ source: 'swe-mux-preview', type: 'location', path: 'docs/page.html?v=1', resources: [] })
  const loaded = reports[reports.length - 1] as { path: string; resources: string[] }
  expect(loaded.path).toBe('docs/page.html?v=1')
  // Both are routed under the prefix; the other origin's image is not this preview's.
  expect(loaded.resources.sort()).toEqual(['css/site.css', 'docs/ref/a.png'])
})

test('a top-level preview document reports nothing', async ({ page }) => {
  await open(page, {}, '<p>opened in its own tab</p>')
  const posted = await responsive(page, async () => {
    let count = 0
    addEventListener('message', () => { count += 1 })
    history.pushState({}, '', 'other')
    await new Promise(resolve => setTimeout(resolve, 100))
    return count
  })
  expect(posted).toBe(0)
})

test('a page that fights the bridge is bounded, not frozen', async ({ page }) => {
  const warnings: string[] = []
  page.on('console', message => { if (message.type() === 'warning') warnings.push(message.text()) })
  await open(page, {}, '<div id="host"></div>')

  const final = await responsive(page, async () => {
    const anchor = document.createElement('a')
    document.getElementById('host')!.appendChild(anchor)
    // Insists on an exact value the bridge would otherwise route: without a bound,
    // the two observers answer each other forever.
    new MutationObserver(() => {
      if (anchor.getAttribute('href') !== '/loop') anchor.setAttribute('href', '/loop')
    }).observe(anchor, { attributes: true })
    anchor.setAttribute('href', '/loop')
    await new Promise(resolve => setTimeout(resolve, 100))
    return anchor.getAttribute('href')
  })

  expect(final).toBe('/loop')
  expect(warnings.some(text => text.includes('swe-mux preview bridge: stopped rewriting'))).toBe(true)
})
