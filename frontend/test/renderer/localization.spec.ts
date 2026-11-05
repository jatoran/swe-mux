import { expect, test } from 'playwright/test'

test('language selection updates mounted Settings, preserves a draft, and survives reload', async ({page}) => {
  await page.goto('/settings-harness.html?localization=1&section=general')
  const picker=page.locator('.language-picker select')
  await expect(picker).toHaveValue('en')
  const folder=page.locator('[data-setting="startup_cwd"] input')
  // The startup field predates explicit data-setting annotations.
  const actualFolder=await folder.count()?folder:page.locator('.settings-content').getByLabel('Startup folder',{exact:true})
  await actualFolder.fill('C:\\localization-unsaved')
  await picker.selectOption('zh-Hans')
  await expect(page.locator('html')).toHaveAttribute('lang','zh-Hans')
  await expect(page.getByRole('tab',{name:'常规',exact:true})).toBeVisible()
  await expect(actualFolder).toHaveValue('C:\\localization-unsaved')
  await picker.selectOption('en')
  await expect(page.getByRole('tab',{name:'General',exact:true})).toBeVisible()
  await expect(actualFolder).toHaveValue('C:\\localization-unsaved')
  await picker.selectOption('zh-Hans')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('lang','zh-Hans')
  await expect(page.locator('.language-picker select')).toHaveValue('zh-Hans')
})

test('translated Settings headings retain stable page navigation on desktop and phone', async ({page}, testInfo) => {
  for(const viewport of [{width:1280,height:900},{width:390,height:844}]){
    await page.setViewportSize(viewport)
    await page.goto('/settings-harness.html?localization=1&section=general')
    await page.locator('.language-picker select').selectOption('zh-Hans')
    await expect(page.locator('[role="tab"][data-settings-tab="general"]')).toHaveAttribute('aria-selected','true')
    await expect(page.locator('.settings-panel > footer [aria-live]')).toHaveText('就绪')
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width)
    await page.screenshot({path:testInfo.outputPath('chinese-general-'+viewport.width+'.png'),fullPage:true})
    if(viewport.width<760)await page.locator('.settings-nav-trigger').click()
    await page.locator('[role="tab"][data-settings-tab="appearance"]').click()
    if(viewport.width<760)await page.locator('.settings-nav-trigger').click()
    await page.locator('[data-settings-subpage-nav="interface"]').click()
    await expect(page.locator('[data-setting="terminal_font_family"]')).toBeVisible()
    await expect(page.locator('h3[data-heading-message]').filter({hasText:'主题'})).toBeVisible()
    if(viewport.width<760)await page.locator('.settings-nav-trigger').click()
    await page.locator('[data-settings-subpage-nav="session-rows"]').click()
    await expect(page.locator('section[data-settings-subpage="session-rows"]').first()).toBeVisible()
  }
})

test('failed language saves preserve the active language and expose retryable feedback', async ({page}) => {
  await page.goto('/settings-harness.html?localization=1&section=general&fail=language')
  await page.locator('.language-picker select').selectOption('zh-Hans')
  await expect(page.locator('.language-picker [role="alert"]')).toContainText('Could not save')
  await expect(page.locator('html')).toHaveAttribute('lang','en')
  await expect(page.locator('.language-picker select')).toHaveValue('en')
})


test('switching languages preserves the mounted terminal, its screen, and its connection',async({page})=>{
  await page.goto('/terminal-transport-harness.html?report=daemon')
  await expect.poll(()=>page.evaluate(()=>(globalThis as any).__screen())).toContain('served by')
  const before=await page.evaluate(()=>{
    ;(globalThis as any).__terminalBefore=document.querySelector('.xterm')
    return {screen:(globalThis as any).__screen(),sockets:(globalThis as any).__sockets()}
  })
  await page.evaluate(async()=>{
    const fixturePath='/test/renderer/localizationFixture.ts'
    const fixture=await import(fixturePath)
    fixture.installTestChinese()
    fixture.setTestLanguage('zh-Hans')
  })
  await expect(page.locator('html')).toHaveAttribute('lang','zh-Hans')
  const after=await page.evaluate(()=>({
    same:document.querySelector('.xterm')===(globalThis as any).__terminalBefore,
    screen:(globalThis as any).__screen(),sockets:(globalThis as any).__sockets(),
  }))
  expect(after.same).toBe(true)
  expect(after.screen).toBe(before.screen)
  expect(after.sockets).toEqual(before.sockets)
})



test('Settings search rebuilds in the selected language and navigates to the translated field',async({page})=>{
  await page.goto('/settings-harness.html?localization=1&section=general')
  await page.locator('.language-picker select').selectOption('zh-Hans')
  const search=page.locator('.settings-search input')
  await search.fill('渲染器')
  const result=page.locator('#settings-search-results [role="option"]').filter({hasText:'渲染器'}).first()
  await expect(result).toBeVisible()
  await result.click()
  await expect(page.locator('[data-setting="terminal_renderer"]')).toBeVisible()
})



test('every Settings tab remains usable after changing language', async ({page}) => {
  const failures: string[] = []
  page.on('pageerror', error => failures.push(error.message))
  page.on('console', message => {
    if(message.text().includes('message_fallback'))failures.push(message.text())
  })
  await page.goto('/settings-harness.html?localization=1&section=general')
  await page.locator('.language-picker select').selectOption('zh-Hans')
  const tabs = await page.locator('[role="tab"][data-settings-tab]').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('data-settings-tab')!))
  for(const tab of tabs){
    await page.locator('[role="tab"][data-settings-tab="'+tab+'"]').click()
    await expect(page.locator('[role="tab"][data-settings-tab="'+tab+'"]')).toHaveAttribute('aria-selected','true')
    await expect(page.locator('.settings-content')).toBeVisible()
  }
  expect(failures).toEqual([])
})
