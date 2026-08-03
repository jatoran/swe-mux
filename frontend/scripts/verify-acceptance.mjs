import { chromium } from 'playwright'

const base = 'http://127.0.0.1:18765'
const initialSpaces = await fetch(`${base}/api/spaces`).then(response => response.json())
const browser = await chromium.launch({ channel: 'msedge', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: base })
const page = await context.newPage()
const errors = []
let terminalFrames = []
page.on('websocket', socket => socket.on('framereceived', event => {
  const payload = event.payload
  terminalFrames.push(Buffer.isBuffer(payload) ? payload.toString('utf8') : String(payload))
}))
page.on('console', message => {
  if (message.type() === 'error' && !message.text().includes('Failed to load resource')) errors.push(message.text())
})
page.on('pageerror', error => errors.push(error.stack || error.message))
page.on('dialog', dialog => {
  errors.push(`Unexpected browser dialog: ${dialog.type()} ${dialog.message()}`)
  void dialog.dismiss()
})

try {
  await page.goto(base, { waitUntil: 'networkidle' })
  await page.locator('.space-group.active .space-row').click({ button: 'right' })
  await page.getByText('New terminal', { exact: true }).waitFor({ state: 'visible' })
  await page.keyboard.press('Escape')
  await page.keyboard.press('Control+Alt+KeyT')
  await page.locator('.xterm-screen').waitFor({ state: 'visible' })
  await page.waitForTimeout(400)
  await page.keyboard.press('Control+Alt+KeyT')
  await page.waitForTimeout(500)
  if (await page.locator('.terminal-pane').count() !== 1) throw new Error('Ordinary new terminal created a split')
  if (await page.locator('.session-row').count() < 2) throw new Error('Displaced session did not remain in sidebar')

  const displaced = page.locator('.session-row:not(.active)')
  const displacedCount = await displaced.count()
  terminalFrames = []
  await displaced.nth(displacedCount - 1).click()
  await page.waitForTimeout(700)
  const replayedText = terminalFrames.join('')
  if (replayedText.includes('[?1;2c')) throw new Error(`Historical terminal query leaked into input: ${JSON.stringify(replayedText)}`)

  const inactiveRows = page.locator('.session-row:not(.active)')
  const inactiveCount = await inactiveRows.count()
  if (!inactiveCount) throw new Error('No inactive session available for explicit split')
  await inactiveRows.nth(inactiveCount - 1).dispatchEvent('contextmenu', { clientX: 220, clientY: 180, button: 2 })
  await page.waitForTimeout(200)
  if (!await page.getByText('Open in focused pane', { exact: true }).count()) {
    throw new Error(`Session context menu did not open; rows=${await page.locator('.session-row').count()} inactive=${await page.locator('.session-row:not(.active)').count()} errors=${errors.join('|')}`)
  }
  // No context menu carries pane geometry any more, so the split runs as the command the
  // palette and keybindings run. Dispatched rather than clicked on purpose: a real pointer
  // would dismiss the menu, and the menu is what makes this session the command's target.
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('mux:command', { detail: 'session.openSplitHorizontal' })))
  await page.waitForTimeout(1000)
  const explicitPaneCount = await page.locator('.terminal-pane').count()
  if (explicitPaneCount !== 2) throw new Error(`Explicit split action failed: ${explicitPaneCount} panes`)
  await page.waitForTimeout(900)

  const terminal = page.locator('.terminal-pane.focused .xterm-helper-textarea')
  await terminal.focus()
  terminalFrames = []
  await terminal.type('Write-Output ')
  await page.evaluate(() => navigator.clipboard.writeText("'MUX_CLIPBOARD_OK'"))
  await page.keyboard.press('Control+KeyV')
  await page.waitForTimeout(150)
  await page.keyboard.press('Enter')
  await page.waitForTimeout(500)
  const terminalText = terminalFrames.join('')
  if (!terminalText.includes('MUX_CLIPBOARD_OK')) throw new Error(`Ctrl+V paste failed: ${JSON.stringify(terminalText)}`)

  await page.keyboard.press('Control+Shift+KeyP')
  await page.locator('.palette').waitFor({ state: 'visible' })
  await page.locator('.palette input').press('Escape')
  await page.locator('.palette').waitFor({ state: 'hidden' })
  await page.locator('.menu-trigger').click()
  await page.getByText('Session history', { exact: true }).click()
  await page.locator('.history-layer').waitFor({ state: 'visible' })
  await page.locator('.history-header > button').click()
  if (await page.locator('.terminal-status').count()) throw new Error('Terminal status footer is present')
  if (!await page.getByText('swe-mux', { exact: true }).isVisible()) throw new Error('swe-mux brand missing')

  const focusedKill = page.locator('.terminal-pane.focused .pane-tools button').filter({ hasText: '×' })
  await focusedKill.click()
  const inlineConfirm = page.locator('.terminal-pane.focused .pane-tools button.confirming')
  if (!await inlineConfirm.isVisible()) throw new Error('First kill click was not inline confirmation')
  if (errors.some(error => error.startsWith('Unexpected browser dialog'))) throw new Error(errors.join('; '))
  await inlineConfirm.click()
  await page.waitForTimeout(500)
  if (await page.locator('.terminal-pane').count() !== 1) throw new Error('Second inline kill click did not execute')
  await page.locator('.menu-trigger').click()
  await page.getByText('Create workspace', { exact: true }).click()
  await page.waitForTimeout(250)
  if (await page.locator('.space-group').count() <= initialSpaces.length) throw new Error('Sidebar space creation failed')
  await page.screenshot({ path: '../.runtime/acceptance.png', fullPage: true })
  if (errors.length) throw new Error(`Browser errors: ${errors.join('; ')}`)
  console.log(JSON.stringify({ ok: true, ctrlV: true, shortcuts: true, defaultPanes: 1, explicitSplit: true, inlineKill: true, history: true, spaces: 'sidebar' }))
} finally {
  const sessions = await fetch(`${base}/api/sessions`).then(response => response.json())
  await Promise.all(sessions.map(session => fetch(`${base}/api/sessions/${session.id}`, { method: 'DELETE' })))
  const spaces = await fetch(`${base}/api/spaces`).then(response => response.json())
  const initialIds = new Set(initialSpaces.map(space => space.id))
  await Promise.all(spaces.filter(space => !initialIds.has(space.id)).map(space => fetch(`${base}/api/spaces/${space.id}`, { method: 'DELETE' })))
  await browser.close()
}
