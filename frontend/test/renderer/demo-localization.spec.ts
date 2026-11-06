import { expect, test, type Page } from 'playwright/test'

const open = async (page: Page, language = 'zh-Hans') => {
  await page.addInitScript(() => localStorage.setItem('swemux-demo-coach-v1', 'done'))
  await page.goto('/demo.html?deterministic=1&lang='+language)
  await page.waitForSelector('.workspace', {timeout:30000})
}
const command = async (page:Page,id:string) => page.evaluate(name => {
  window.dispatchEvent(new CustomEvent('mux:command',{detail:name}))
},id)

test('Chinese demo switches language without remounting terminals or changing keymaps',async({page})=>{
  const workers: string[] = []
  page.on('request', request => { if (request.url().endsWith('/sw.js')) workers.push(request.url()) })
  await open(page)
  await expect(page.locator('html')).toHaveAttribute('lang','zh-Hans')
  await expect(page.locator('[data-demo-language]')).toHaveValue('zh-Hans')
  await expect(page.locator('.demo-bar-pick')).toContainText('浏览工作区导览')
  await page.locator('.demo-keymaps button').filter({hasText:/^tmux$/}).click()
  const before=await page.evaluate(async()=>{
    ;(window as any).__localizedTerminal=document.querySelector('.xterm')
    return await (await fetch('/api/keybindings')).json()
  })
  await page.locator('[data-demo-language]').selectOption('en')
  await expect(page.locator('html')).toHaveAttribute('lang','en')
  await expect(page.locator('.demo-keymaps .demo-keymap-hint')).toContainText('split right')
  expect(await page.evaluate(()=>document.querySelector('.xterm')===(window as any).__localizedTerminal)).toBe(true)
  const after=await page.evaluate(async()=>await(await fetch('/api/keybindings')).json())
  expect(after.resolved).toEqual(before.resolved)
  expect(after.preset).toBe('tmux')
  expect(workers).toEqual([])
  await page.locator('[data-demo-language]').selectOption('zh-Hans')
  await expect(page).toHaveURL(/lang=zh-Hans/)
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('lang','zh-Hans')
})

test('desktop and phone share language, localized settings, and dialog identity',async({browser})=>{
  const context=await browser.newContext()
  await context.addInitScript(()=>localStorage.setItem('swemux-demo-coach-v1','done'))
  const desktop=await context.newPage(),phone=await context.newPage()
  await desktop.setViewportSize({width:1280,height:900})
  await phone.setViewportSize({width:390,height:844})
  await open(desktop,'en'); await open(phone,'en')
  await desktop.locator('[data-demo-language]').selectOption('zh-Hans')
  await expect(phone.locator('html')).toHaveAttribute('lang','zh-Hans')
  await desktop.locator('.menu-trigger').click()
  await desktop.locator('.menu-row').filter({hasText:/^设置/}).click()
  await expect(desktop.locator('[data-dialog-id="settings"]')).toBeVisible()
  await expect(phone.locator('[data-dialog-id="settings"]')).toBeVisible({timeout:15000})
  await expect(desktop.locator('[role="tab"][data-settings-tab="general"]')).toContainText('常规')
  expect(await phone.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await context.close()
})

test('IME candidate keys do not dismiss Settings, navigate search, or record shortcuts',async({page})=>{
  await page.goto('/settings-harness.html?localization=1&section=general')
  await page.locator('.language-picker select').selectOption('zh-Hans')
  const search=page.locator('.settings-search input')
  await search.fill('快捷键')
  await expect(page.locator('#settings-search-results [role="option"]').first()).toBeVisible()
  for(const marker of [{isComposing:true},{keyCode:229}]){
    for(const key of ['Enter','Escape','ArrowDown',' ']){
      await search.dispatchEvent('keydown',{key,code:key===' '?'Space':key,...marker,bubbles:true,cancelable:true})
    }
    await expect(page.locator('.settings-panel')).toBeVisible()
    await expect(search).toHaveValue('快捷键')
  }
  await search.fill('键盘快捷键')
  await page.locator('#settings-search-results [role="option"]').first().click()
  await page.locator('.keybinding-command').first().click()
  await expect(page.locator('.keybinding-capture')).toBeVisible()
  await page.evaluate(()=>{
    window.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',isComposing:true,bubbles:true,cancelable:true}))
    window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',code:'Escape',keyCode:229,bubbles:true,cancelable:true}))
  })
  await expect(page.locator('.keybinding-capture')).toBeVisible()
})

test('browser IME commits Chinese into the command palette without running a command',async({page,context})=>{
  await open(page)
  await command(page,'palette.open')
  const input=page.locator('.palette input')
  await input.focus()
  const cdp=await context.newCDPSession(page)
  await cdp.send('Input.imeSetComposition',{text:'对话',selectionStart:2,selectionEnd:2})
  await input.dispatchEvent('keydown',{key:'Enter',code:'Enter',isComposing:true,bubbles:true,cancelable:true})
  await expect(page.locator('.palette')).toBeVisible()
  await cdp.send('Input.insertText',{text:'对话'})
  await expect(input).toHaveValue('对话')
  await expect(page.locator('.palette')).toBeVisible()
  await cdp.detach()
})

for(const language of ['en','zh-Hans']){
  test('walkthrough search and spotlight targets work in '+language,async({page})=>{
    await open(page,language)
    await page.evaluate(()=>void (window as any).__demoDirector.start('keymap'))
    await expect(page.locator('.keymap-presets')).toBeVisible({timeout:20000})
    await expect(page.locator('[data-keymap-preset="tmux"]')).toBeVisible()
    await page.evaluate(()=>(window as any).__demoDirector.stop('dismissed'))
    const messages=await page.evaluate(()=>JSON.parse(localStorage.getItem('swemux-demo-diagnostics-v1')||'[]'))
    expect(messages.filter((m:any)=>m.severity==='error')).toEqual([])
  })
}
