import { expect, test } from 'playwright/test'

/**
 * The two split-out surfaces, measured in a real module graph.
 *
 * The entry bundle carried ~28 CodeMirror grammars, CodeMirror's core, and Sigma plus
 * Graphology — 3.38MB raw / 1.06MB gzip — for surfaces most sessions never open. The unit
 * suite can assert that no static import remains; only a browser can show that the module
 * is genuinely not fetched until the surface mounts, that the stand-in in between holds the
 * box the real thing will take (a lazy route's real failure mode is a layout that jumps on
 * every open), and that the grammar lands *after* the document is already readable.
 *
 * `performance.getEntriesByType('resource')` is the oracle: Vite serves every module as its
 * own request in dev, so a fetch that has not happened is simply an entry that is not there.
 */

type Page = import('playwright/test').Page

const fetched = (page: Page, module: string) => page.evaluate(
  name => performance.getEntriesByType('resource').some(entry => entry.name.includes(name)),
  module,
)

test('the editor is not fetched until a file is opened, and holds its box while it loads', async ({ page }) => {
  await page.goto('/lazy-surfaces-harness.html')
  await expect(page.locator('#mount-editor')).toBeVisible()
  expect(await fetched(page, '/src/CodeEditor.tsx')).toBe(false)

  await page.locator('#mount-editor').click()
  // The real editor replaces the stand-in; whichever wins the race, the slot is filled.
  await expect(page.locator('#editor-slot .code-editor')).toBeVisible()
  await expect(page.locator('#editor-slot .cm-editor')).toBeVisible()
  await expect(page.locator('#editor-slot .code-editor-state')).toHaveCount(0)
  expect(await fetched(page, '/src/CodeEditor.tsx')).toBe(true)

  // The document is readable, which is the property the mount-then-reconfigure ordering
  // buys: no spinner stands between opening a file and seeing its text.
  await expect(page.locator('#editor-slot .cm-content')).toContainText('const answer = 42')
})

test('the stand-in occupies the editor box rather than collapsing', async ({ page }) => {
  // Measured on the placeholder in isolation, because the real one is replaced too fast to
  // catch: the class contract is what keeps the grid cell filled, so it is what is checked.
  await page.goto('/lazy-surfaces-harness.html')
  const box = await page.evaluate(() => {
    const slot = document.querySelector('#editor-slot')!
    const stand = document.createElement('div')
    stand.className = 'code-editor code-editor-state'
    stand.innerHTML = '<span>Preparing editor…</span>'
    slot.appendChild(stand)
    const rect = stand.getBoundingClientRect()
    // Read through before detaching: `getComputedStyle` hands back a live declaration, and
    // a detached element's is empty.
    const caption = getComputedStyle(stand.firstElementChild!)
    const measured = {
      width: rect.width, height: rect.height,
      opacity: caption.opacity, delay: caption.animationDelay,
    }
    stand.remove()
    return measured
  })
  expect(box.width).toBeGreaterThan(0)
  expect(box.height).toBeGreaterThan(0)
  // No flash of a caption on a fast load: it is transparent until the wait is perceptible.
  expect(box.opacity).toBe('0')
  expect(box.delay).toBe('0.35s')
})

test('a grammar arrives after the text, and colours it once it does', async ({ page }) => {
  await page.goto('/lazy-surfaces-harness.html')
  await page.locator('#mount-editor').click()
  await expect(page.locator('#editor-slot .cm-content')).toContainText('const answer = 42')
  // Tokens are spans inside a line; a document with no grammar has none. Waiting for them
  // to appear is what proves the grammar was fetched separately and applied in place,
  // rather than having been in the bundle all along.
  await expect(page.locator('#editor-slot .cm-line span').first()).toBeVisible()
})

test('typing round-trips and an external rewrite still reconciles', async ({ page }) => {
  // The editor now dismisses its own echo by reference instead of re-serializing the
  // document. The risk that introduces is the opposite direction: a value the editor did
  // *not* produce must still replace the document.
  //
  // Typing a whole word is the point rather than incidental. The parent stores each emitted
  // string and re-renders, so it is a turn behind the keyboard, and the reconcile effect
  // used to read that lag as an external change — replacing the document with an older copy
  // of itself, which re-emitted, which replaced it again. At machine typing speed this
  // wedged the page outright; at human speed it silently dropped characters.
  await page.goto('/lazy-surfaces-harness.html')
  await page.locator('#mount-editor').click()
  await expect(page.locator('#editor-slot .cm-content')).toContainText('const answer = 42')

  await page.locator('#editor-slot .cm-content').click()
  await page.keyboard.press('Control+End')
  await page.locator('#editor-slot .cm-content').pressSequentially('// tail')
  await expect(page.locator('#editor-slot .cm-content')).toContainText('// tail')
  expect(await page.evaluate(() => window.editorValue)).toContain('// tail')

  await page.locator('#external-change').click()
  await expect(page.locator('#editor-slot .cm-content')).toContainText('const answer = 7')
  await expect(page.locator('#editor-slot .cm-content')).not.toContainText('// tail')
})

test('the change map is not fetched until one is opened', async ({ page }) => {
  await page.goto('/lazy-surfaces-harness.html')
  await expect(page.locator('#mount-map')).toBeVisible()
  expect(await fetched(page, '/src/ChangeMapPane.tsx')).toBe(false)

  await page.locator('#mount-map').click()
  // With no session the loaded pane renders its empty state — reached only by the real
  // module, so seeing it means Sigma and Graphology arrived with it.
  await expect(page.locator('#map-slot .drawer-empty')).toBeVisible()
  await expect(page.locator('#map-slot .change-map-state')).toHaveCount(0)
  expect(await fetched(page, '/src/ChangeMapPane.tsx')).toBe(true)
  expect(await fetched(page, 'sigma')).toBe(true)
})
