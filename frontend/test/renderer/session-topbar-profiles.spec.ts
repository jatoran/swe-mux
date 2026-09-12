import { expect, test, type Page } from 'playwright/test'
import { addSessionTopbarRow, defaultSessionTopbarConfig } from '../../src/sessionTopbarConfig'

const url='/settings-harness.html?section=Appearance&setting=session_topbar'
const profiles=(page:Page)=>page.getByRole('group',{name:'Editing session top bar layout'})
const previewRows=(page:Page)=>page.locator('.session-topbar-preview .session-topbar-row')
const writes=(page:Page)=>page.evaluate(()=>window.settingsCalls.filter(call=>call.method==='PUT'&&/^\/api\/settings\//.test(call.path)))

for(const device of ['desktop','mobile'] as const){
  test(`top bar profiles inherit, fork, persist, and rejoin on ${device}`,async({page})=>{
    await page.setViewportSize(device==='desktop'?{width:1280,height:900}:{width:390,height:844})
    await page.addInitScript(profile=>localStorage.setItem('mux.device-mode.v1',profile),device)
    await page.goto(url)
    await expect(profiles(page).getByRole('button',{name:device==='desktop'?'Desktop':'Mobile',exact:true})).toHaveAttribute('aria-pressed','true')
    await profiles(page).getByRole('button',{name:'Desktop',exact:true}).click()
    await page.getByRole('button',{name:'Add row',exact:true}).click()
    await expect(previewRows(page)).toHaveCount(2)
    await expect.poll(async()=>(await writes(page)).length).toBe(1)
    await profiles(page).getByRole('button',{name:'Mobile',exact:true}).click()
    await expect(previewRows(page)).toHaveCount(2)
    await expect(page.locator('.topbar-profile-status')).toContainText('Using the desktop layout')
    expect(await writes(page)).toHaveLength(1)
    await page.getByRole('button',{name:'Add row',exact:true}).click()
    await expect(previewRows(page)).toHaveCount(3)
    await expect(page.locator('.topbar-profile-status')).toContainText('Using a separate mobile layout')
    await expect.poll(async()=>(await writes(page)).map(call=>call.path)).toEqual(['/api/settings/desktop','/api/settings/mobile'])
    await profiles(page).getByRole('button',{name:'Desktop',exact:true}).click()
    await page.getByRole('button',{name:'Reset to default',exact:true}).click()
    await expect(previewRows(page)).toHaveCount(1)
    await page.reload()
    await profiles(page).getByRole('button',{name:'Mobile',exact:true}).click()
    await expect(previewRows(page)).toHaveCount(3)
    await page.getByRole('button',{name:'Use desktop layout',exact:true}).click()
    await expect(previewRows(page)).toHaveCount(1)
    await expect(page.locator('.topbar-profile-status')).toContainText('Using the desktop layout')
    await profiles(page).getByRole('button',{name:'Desktop',exact:true}).click()
    await page.getByRole('button',{name:'Add row',exact:true}).click()
    await profiles(page).getByRole('button',{name:'Mobile',exact:true}).click()
    await expect(previewRows(page)).toHaveCount(2)
    await page.reload()
    await profiles(page).getByRole('button',{name:'Mobile',exact:true}).click()
    await expect(previewRows(page)).toHaveCount(2)
    await expect(page.locator('.topbar-profile-status')).toContainText('Using the desktop layout')
  })
}

test('inherited preview follows remote desktop edits, including a deliberately empty bar',async({page})=>{
  await page.goto(url)
  await profiles(page).getByRole('button',{name:'Mobile',exact:true}).click()
  const config={...defaultSessionTopbarConfig(),rows:[{left:[],right:[],separator:'none'}]}
  await page.evaluate(async config=>{
    await fetch('/api/settings/desktop',{method:'PUT',body:JSON.stringify({sessionTopbar:config})})
    const path='/src/deviceSettings.ts'
    await (await import(/* @vite-ignore */ path)).loadSettings()
  },config)
  await expect(page.locator('.session-topbar-preview .session-topbar-item')).toHaveCount(0)
  await expect(page.locator('.topbar-profile-status')).toContainText('Using the desktop layout')
  await expect(page.locator('.session-topbar-preview').getByRole('button',{name:'More actions'})).toBeVisible()
})

test('live panes select the device profile and retain mobile overrides across resizing',async({page})=>{
  await page.addInitScript(()=>localStorage.setItem('swemux-demo-coach-v1','done'))
  await page.setViewportSize({width:1280,height:900})
  await page.goto('/demo.html?deterministic=1')
  await expect(page.locator('.terminal-pane').first()).toBeVisible({timeout:30_000})
  const desktop=addSessionTopbarRow(defaultSessionTopbarConfig())
  const mobile=addSessionTopbarRow(desktop)
  await page.evaluate(async configs=>{
    for(const [profile,sessionTopbar] of Object.entries(configs))await fetch(`/api/settings/${profile}`,{method:'PUT',body:JSON.stringify({sessionTopbar})})
    const path='/src/deviceSettings.ts'
    await (await import(/* @vite-ignore */ path)).loadSettings()
  },{desktop,mobile})
  const rows=()=>page.locator('.terminal-pane:visible .session-topbar').first().locator('.session-topbar-row')
  await expect(rows()).toHaveCount(2)
  await page.evaluate(async()=>{
    const path='/src/deviceMode.ts'
    ;(await import(/* @vite-ignore */ path)).setDevicePreference('mobile')
  })
  await expect(rows()).toHaveCount(3)
  await page.setViewportSize({width:390,height:844})
  await expect(rows()).toHaveCount(3)
  await page.setViewportSize({width:1280,height:900})
  await expect(rows()).toHaveCount(3)
  await page.evaluate(async()=>{
    const path='/src/deviceMode.ts'
    ;(await import(/* @vite-ignore */ path)).setDevicePreference('desktop')
  })
  await expect(rows()).toHaveCount(2)
})

test('failed mobile saves report the error and keep bounded profile diagnostics',async({page})=>{
  await page.goto(url)
  await profiles(page).getByRole('button',{name:'Mobile',exact:true}).click()
  await page.evaluate(()=>{
    const original=window.fetch
    window.fetch=async(input,init)=>String(input)==='/api/settings/mobile'
      ?new Response(JSON.stringify({error:'offline'}),{status:503}):original(input,init)
  })
  await page.getByRole('button',{name:'Add row',exact:true}).click()
  await expect(page.locator('.settings-inline-error')).toContainText('Could not save the top bar layout')
  const entries=await page.evaluate(()=>JSON.parse(localStorage.getItem('mux.session-topbar-diagnostics.v1')||'[]'))
  expect(entries.map((entry:{operation:string})=>entry.operation)).toEqual(['save-started','save-failed'])
  expect(entries.every((entry:{profile:string})=>entry.profile==='mobile')).toBe(true)
  expect(entries[1].sequence).toBe(entries[0].sequence)
  await page.evaluate(async()=>{
    const path='/src/sessionTopbarPrefs.ts'
    const prefs=await import(/* @vite-ignore */ path)
    for(let index=0;index<34;index++)await prefs.resetSessionTopbarConfig('mobile').catch(()=>{})
  })
  const retained=await page.evaluate(()=>JSON.parse(localStorage.getItem('mux.session-topbar-diagnostics.v1')||'[]'))
  expect(retained).toHaveLength(64)
  expect(retained[0].sequence).toBeGreaterThan(entries[0].sequence)
  expect(retained.at(-1).operation).toBe('save-failed')
})
