import { expect, test, type Page } from 'playwright/test'

async function mode(page: Page) {
  return page.locator('html').evaluate(root => ({
    layout: root.dataset.workspaceLayout, profile: root.dataset.deviceProfile,
  }))
}

async function boot(page: Page) {
  await page.addInitScript(() => localStorage.setItem('swemux-demo-coach-v1', 'done'))
  await page.goto('/demo.html?deterministic=1')
  await expect(page.locator('.terminal-pane').first()).toBeVisible({timeout:30_000})
}

async function swipe(page: Page, direction: 1 | -1) {
  const box = await page.locator('.mobile-unified-active .terminal-surface').boundingBox()
  if (!box) throw new Error('Missing mobile terminal surface')
  const cdp = await page.context().newCDPSession(page)
  const x = box.x + box.width / 2, y = box.y + box.height / 2
  const points = (offset: number) => [
    {x:x+offset,y,id:1},
  ]
  await cdp.send('Input.dispatchTouchEvent', {type:'touchStart',touchPoints:points(0)})
  for (let step=1; step<=5; step++) {
    await cdp.send('Input.dispatchTouchEvent', {type:'touchMove',touchPoints:points(direction*step*24)})
  }
  await cdp.send('Input.dispatchTouchEvent', {type:'touchEnd',touchPoints:[]})
  await cdp.detach()
}

test.describe('wide touch devices', () => {
  test.use({hasTouch:true,isMobile:true,viewport:{width:900,height:900}})

  test('unfolding keeps the terminal mounted, mobile settings, chrome and gestures', async ({page}) => {
    await boot(page)
    await expect.poll(() => mode(page)).toEqual({layout:'mobile',profile:'mobile'})
    await expect(page.locator('.mobile-toolbar')).toBeVisible()
    await expect(page.locator('.app-topbar')).toBeHidden()
    await expect(page.locator('.mobile-unified-active .terminal-pane')).toHaveCount(1)
    await page.screenshot({path:test.info().outputPath('wide-mobile.png')})
    await page.locator('.mobile-unified-active .terminal-pane').evaluate(node => node.setAttribute('data-fold-probe','retained'))
    const keyboard = page.locator('.mobile-unified-active .kbd-toggle')
    const draft = page.locator('.mobile-terminal-draft textarea')
    for (let attempt=0; attempt<3 && !await draft.isVisible(); attempt++) {
      const before = await keyboard.getAttribute('class')
      await keyboard.click()
      await expect(keyboard).not.toHaveAttribute('class',before!)
    }
    await expect(draft).toBeVisible()
    await draft.fill('Keep this draft while folding and rotating.')
    for (const viewport of [{width:390,height:844},{width:1100,height:780},{width:780,height:1100}]) {
      await page.setViewportSize(viewport)
      await expect.poll(() => mode(page)).toEqual({layout:'mobile',profile:'mobile'})
      await expect(page.locator('.mobile-unified-active .terminal-pane')).toHaveAttribute('data-fold-probe','retained')
      await expect(page.locator('.mobile-toolbar')).toBeVisible()
      await expect(draft).toHaveValue('Keep this draft while folding and rotating.')
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    }
    await keyboard.click()
    await expect(draft).toBeHidden()
    // The demo binds one-finger horizontal swipes to the two drawers.
    await swipe(page, 1)
    await expect(page.locator('.sidebar')).toHaveClass(/open/)
    await page.locator('.sidebar-scrim').click()
    await swipe(page, -1)
    await expect(page.getByRole('dialog',{name:'Utility drawer',exact:true})).toBeVisible()
    const diagnostics = await page.evaluate(() => JSON.parse(localStorage.getItem('mux.device-mode-diagnostics.v1') || '[]'))
    expect(diagnostics.length).toBeGreaterThan(1)
    expect(diagnostics.every((entry: {profile:string}) => entry.profile === 'mobile')).toBe(true)
  })

  test('desktop override retains soft-keyboard reservation on a wide touch device', async ({page}) => {
    await page.addInitScript(() => localStorage.setItem('mux.device-mode.v1','desktop'))
    await boot(page)
    await expect.poll(() => mode(page)).toEqual({layout:'desktop',profile:'desktop'})
    await expect(page.locator('.app-topbar')).toBeVisible()
    const reserve = await page.locator('.terminal-surface').first().evaluate(node => {
      node.classList.add('keyboard-reserved')
      ;(node as HTMLElement).style.setProperty('--terminal-keyboard-reserve','180px')
      return getComputedStyle(node).marginBottom
    })
    expect(reserve).toBe('180px')
    expect(await page.locator('html').getAttribute('data-touch-input')).toBe('true')
  })
})

test('desktop resizing keeps desktop preferences while compacting navigation', async ({page}) => {
  await page.setViewportSize({width:1100,height:800})
  await boot(page)
  await expect.poll(() => mode(page)).toEqual({layout:'desktop',profile:'desktop'})
  await page.setViewportSize({width:600,height:800})
  await expect.poll(() => mode(page)).toEqual({layout:'mobile',profile:'desktop'})
  await expect(page.locator('.mobile-toolbar')).toBeVisible()
  await page.setViewportSize({width:1100,height:800})
  await expect.poll(() => mode(page)).toEqual({layout:'desktop',profile:'desktop'})
  await expect(page.locator('.app-topbar')).toBeVisible()
})

test('the settings override applies immediately, persists and synchronizes browser tabs', async ({page,context}) => {
  await page.setViewportSize({width:1100,height:800})
  await page.goto('/settings-harness.html?section=appearance')
  const section = page.locator('section[data-settings-subpage]').filter({has:page.getByRole('heading',{name:'Device mode',exact:true})})
  await expect(section).toBeVisible()
  const second = await context.newPage()
  await second.goto('/settings-harness.html?section=appearance')
  await section.locator('.dropdown-trigger').click()
  await page.getByRole('option',{name:'Mobile',exact:true}).click()
  await expect.poll(() => mode(page)).toEqual({layout:'mobile',profile:'mobile'})
  await expect.poll(() => mode(second)).toEqual({layout:'mobile',profile:'mobile'})
  await page.reload()
  await expect.poll(() => mode(page)).toEqual({layout:'mobile',profile:'mobile'})
  await expect(page.locator('.settings-nav-trigger')).toBeVisible()
  await section.locator('.dropdown-trigger').click()
  await page.getByRole('option',{name:'Auto',exact:true}).click()
  await expect.poll(() => mode(page)).toEqual({layout:'desktop',profile:'desktop'})
})

test('a touchscreen laptop keeps desktop layout when its primary pointer is a mouse', async ({page}) => {
  await page.addInitScript(() => Object.defineProperty(navigator,'maxTouchPoints',{get:()=>10}))
  await page.setViewportSize({width:1100,height:800})
  await boot(page)
  await expect.poll(() => mode(page)).toEqual({layout:'desktop',profile:'desktop'})
})

test('a blocked preference write applies for this page and reports that it cannot persist', async ({page}) => {
  await page.addInitScript(() => {
    const write = Storage.prototype.setItem
    Storage.prototype.setItem = function(key,value) {
      if (key === 'mux.device-mode.v1') throw new DOMException('Blocked','SecurityError')
      return write.call(this,key,value)
    }
  })
  await page.goto('/settings-harness.html?section=appearance')
  const section = page.locator('section[data-settings-subpage]').filter({has:page.getByRole('heading',{name:'Device mode',exact:true})})
  await section.locator('.dropdown-trigger').click()
  await page.getByRole('option',{name:'Mobile',exact:true}).click()
  await expect.poll(() => mode(page)).toEqual({layout:'mobile',profile:'mobile'})
  await expect(section.getByRole('status')).toContainText('Browser storage is unavailable')
})

test('device diagnostics recover corrupt storage and retain a bounded transition history', async ({page}) => {
  await page.addInitScript(() => localStorage.setItem('mux.device-mode-diagnostics.v1','corrupt'))
  await page.goto('/settings-harness.html?section=appearance')
  const history = await page.evaluate(async () => {
    const modulePath = '/src/deviceMode.ts'
    const policy = await import(modulePath)
    for (let index=0; index<70; index++) policy.setDevicePreference(index%2 ? 'desktop' : 'mobile')
    return JSON.parse(localStorage.getItem('mux.device-mode-diagnostics.v1') || '[]')
  })
  expect(history).toHaveLength(64)
  expect(history.every((entry: {component:string;at:string;pageId:string}) =>
    entry.component==='device-mode' && !!entry.pageId && Number.isFinite(Date.parse(entry.at)))).toBe(true)
  expect(history.at(-1).preference).toBe('desktop')
})
