/**
 * Record the marketing demo playing a named scenario, headlessly and reproducibly.
 *
 * This exists to replace a category of risk rather than to save time. The hero film and
 * the landing page's stills were captured by driving a *real* install
 * (`trailer/capture_env.py`), which means every one of them had to be checked, by a
 * person, for a path or a session name or a number that came from a real machine - and
 * every re-capture had to be checked again. Here there is nothing to check: the demo's
 * install is invented by construction (`frontend/src/demo/fixtures.ts`), it talks to no
 * network, and the whole run is a function of a seed. The "no field copied from a real
 * install" risk class is not mitigated for these assets; it does not exist.
 *
 * The second thing it buys is that the output cannot drift from the product. It drives
 * the **committed** `site/demo/` bundle over a plain static server, which is the exact
 * artifact GitHub Pages uploads - so a still that disagrees with the product means the
 * bundle is stale, and rebuilding is the fix.
 *
 *   node scripts/capture-demo.mjs --scenario queue
 *   node scripts/capture-demo.mjs --scenario orchestrate --surface phone
 *   node scripts/capture-demo.mjs --scenario queue --stills-only     # no video, fast
 *   node scripts/capture-demo.mjs --scenario queue --check           # determinism gate
 *
 * `--check` plays the scenario twice and compares the store fingerprints. It is the CI
 * form: it needs no video encoder, it takes one run's worth of extra time, and it fails
 * on exactly the thing that makes every other capture worthless - a demo that is not the
 * same demo twice.
 */
import { createReadStream } from 'node:fs'
import { mkdir, rm, readdir, rename, stat, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { dirname, extname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const SITE = resolve(here, '../../site')
const DEFAULT_OUT = resolve(here, '../../trailer/demo-capture')

/** The demo build is the same asset set the app ships, so this list is the app's. */
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.wasm': 'application/wasm',
  '.map': 'application/json; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
}

const SURFACES = {
  desktop: { width: 1440, height: 900, deviceScaleFactor: 2 },
  phone: { width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true },
}

function parseArgs(argv) {
  const options = {
    scenario: 'queue',
    surface: 'desktop',
    out: DEFAULT_OUT,
    seed: '',
    port: 0,
    stillsOnly: false,
    check: false,
    headed: false,
  }
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index]
    const value = () => argv[(index += 1)]
    if (flag === '--scenario') options.scenario = value()
    else if (flag === '--surface') options.surface = value()
    else if (flag === '--out') options.out = resolve(value())
    else if (flag === '--seed') options.seed = value()
    else if (flag === '--port') options.port = Number(value())
    else if (flag === '--stills-only') options.stillsOnly = true
    else if (flag === '--check') options.check = true
    else if (flag === '--headed') options.headed = true
    else throw new Error(`unknown flag: ${flag}`)
  }
  if (!SURFACES[options.surface]) throw new Error(`unknown surface: ${options.surface}`)
  return options
}

/**
 * A static server over `site/`, on an OS-allocated port.
 *
 * Port 0 rather than a number, and this is the same rule the rest of this repository's
 * test tiers follow: a fixed port is what makes two checkouts, or a checkout and CI,
 * silently drive each other's build. Nothing here may collide with the operator's daemon
 * on 8765 either, which a hardcoded alternate would only postpone.
 */
async function serveSite(port) {
  const server = createServer((request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1')
    let path = decodeURIComponent(url.pathname)
    if (path.endsWith('/')) path += 'index.html'
    const file = join(SITE, path)
    if (!file.startsWith(SITE)) { response.writeHead(403).end(); return }
    stat(file)
      .then(info => {
        if (!info.isFile()) throw new Error('not a file')
        response.writeHead(200, {
          'Content-Type': MIME[extname(file)] || 'application/octet-stream',
          'Cache-Control': 'no-store',
        })
        createReadStream(file).pipe(response)
      })
      .catch(() => { response.writeHead(404, { 'Content-Type': 'text/plain' }).end('not found') })
  })
  await new Promise((done, fail) => {
    server.on('error', fail)
    server.listen(port, '127.0.0.1', done)
  })
  return { server, port: server.address().port }
}

const closeServer = server => new Promise(done => server.close(done))

/** Read the director's snapshot out of the page, without assuming it exists yet. */
const readState = page => page.evaluate(() => {
  const handle = window.__demoDirector
  if (!handle) return null
  const view = handle.snapshot()
  return { running: view.running, index: view.index, total: view.total, say: view.say, scenarioId: view.scenarioId }
})

/**
 * Play one scenario end to end, shooting a still at each beat.
 *
 * The stills are driven by the director's own beat counter rather than by a timer: a
 * capture that slept "about two seconds" between shots is wrong whenever the machine is
 * busy, and produces frames nobody can label. Here every still is named for the beat it
 * belongs to, which is also what makes a diff between two runs readable.
 */
async function playOnce({ browser, origin, options, outDir, video }) {
  const surface = SURFACES[options.surface]
  const context = await browser.newContext({
    viewport: { width: surface.width, height: surface.height },
    deviceScaleFactor: surface.deviceScaleFactor,
    isMobile: Boolean(surface.isMobile),
    hasTouch: Boolean(surface.hasTouch),
    reducedMotion: 'no-preference',
    ...(video ? { recordVideo: { dir: outDir, size: { width: surface.width, height: surface.height } } } : {}),
  })
  const page = await context.newPage()
  const query = new URLSearchParams({
    deterministic: '1',
    scenario: options.scenario,
    // Only the rig sets this: it draws a marker where a *real* press landed, which is
    // what makes a recorded interaction legible as one rather than as the UI twitching.
    highlightInput: '1',
  })
  if (options.seed) query.set('seed', options.seed)

  const failures = []
  page.on('pageerror', error => failures.push(String(error)))

  await page.goto(`${origin}/demo/?${query}`, { waitUntil: 'load' })
  // The app's own first paint, then the director's start delay. Waiting on the workspace
  // rather than on a duration is what keeps this honest on a slow runner.
  await page.waitForSelector('.workspace', { timeout: 30_000 })
  await page.waitForFunction(() => window.__demoDirector?.snapshot().running === true, null, { timeout: 30_000 })

  const stills = []
  let lastIndex = -1
  const deadline = Date.now() + 120_000
  for (;;) {
    const view = await readState(page)
    if (!view) break
    if (view.index !== lastIndex && view.index > 0) {
      lastIndex = view.index
      const name = `beat-${String(view.index).padStart(2, '0')}.png`
      // A beat's act is performed after its caption is published, and the ghost cursor
      // takes ~0.6s to travel and press, so a still shot the instant the counter moves
      // catches the screen *before* the thing the caption describes. This waits it out.
      await page.waitForTimeout(900)
      await page.screenshot({ path: join(outDir, name) })
      stills.push({ beat: view.index, of: view.total, say: view.say, file: name })
    }
    if (!view.running) break
    if (Date.now() > deadline) throw new Error(`scenario "${options.scenario}" did not finish inside 120s`)
    await page.waitForTimeout(120)
  }

  const fingerprint = await page.evaluate(() => window.__demoDirector.fingerprint())
  await page.close()
  await context.close()

  let videoFile = ''
  if (video) {
    // Playwright names the file after the page's guid; rename it to the scenario so a
    // second run overwrites rather than accumulating.
    const written = (await readdir(outDir)).filter(name => name.endsWith('.webm'))
    if (written.length) {
      videoFile = `${options.scenario}-${options.surface}.webm`
      await rename(join(outDir, written[0]), join(outDir, videoFile))
    }
  }
  return { fingerprint, stills, videoFile, failures }
}

async function main() {
  const options = parseArgs(process.argv.slice(2))
  const { server, port } = await serveSite(options.port)
  const origin = `http://127.0.0.1:${port}`
  const outDir = join(options.out, `${options.scenario}-${options.surface}`)
  await rm(outDir, { recursive: true, force: true })
  await mkdir(outDir, { recursive: true })

  const browser = await chromium.launch({
    headless: !options.headed,
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
  })
  try {
    const first = await playOnce({
      browser, origin, options, outDir, video: !options.stillsOnly && !options.check,
    })
    if (first.failures.length) {
      throw new Error(`the demo raised ${first.failures.length} page error(s):\n${first.failures.join('\n')}`)
    }

    let identical = null
    if (options.check) {
      const secondDir = join(outDir, 'second')
      await mkdir(secondDir, { recursive: true })
      const second = await playOnce({ browser, origin, options, outDir: secondDir, video: false })
      identical = first.fingerprint === second.fingerprint
      if (!identical) {
        await writeFile(join(outDir, 'fingerprint-a.json'), first.fingerprint)
        await writeFile(join(outDir, 'fingerprint-b.json'), second.fingerprint)
        throw new Error(
          'deterministic mode is not deterministic: two runs of '
          + `"${options.scenario}" produced different stores. See fingerprint-a/b.json.`,
        )
      }
      await rm(secondDir, { recursive: true, force: true })
    }

    await writeFile(join(outDir, 'manifest.json'), `${JSON.stringify({
      scenario: options.scenario,
      surface: options.surface,
      seed: options.seed || 'default',
      deterministic: true,
      video: first.videoFile || null,
      stills: first.stills,
      determinismChecked: identical,
      // The fingerprint is the artifact's provenance: it says exactly which fixture
      // state produced these frames, which is the question a stale capture raises.
      fingerprint: JSON.parse(first.fingerprint),
    }, null, 2)}\n`)

    process.stdout.write(
      `captured ${options.scenario} (${options.surface}): ${first.stills.length} stills`
      + `${first.videoFile ? `, ${first.videoFile}` : ''}`
      + `${identical === null ? '' : ', determinism verified'}\n  ${outDir}\n`,
    )
  } finally {
    await browser.close()
    await closeServer(server)
  }
}

main().catch(error => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`)
  process.exitCode = 1
})
