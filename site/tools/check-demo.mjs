import { chromium } from '../../frontend/node_modules/playwright/index.mjs'
import { serveSite } from '../../frontend/scripts/capture-demo.mjs'
import { mkdir, writeFile } from 'node:fs/promises'
import assert from 'node:assert/strict'

const {server, port} = await serveSite()
const browser = await chromium.launch({headless:true, args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']})
const base = `http://127.0.0.1:${port}`
const failures=[]
try {
  await mkdir('.tmp/review', {recursive:true})
  const page=await browser.newPage({viewport:{width:1440,height:1000}})
  page.on('pageerror',error=>failures.push(String(error)))
  await page.goto(base)
  await page.locator('#demo').scrollIntoViewIfNeeded()
  await page.locator('[data-preset="tmux"]').waitFor({timeout:30000})
  await page.screenshot({path:'.tmp/review/home-desktop.png'})
  await page.locator('[data-preset="tmux"]').click()
  const frame=page.frames().find(frame=>frame.url().includes('/demo/'))
  await frame.waitForFunction(()=>document.querySelectorAll('.terminal-pane').length>0)
  await frame.waitForFunction(()=>window.__demoControls.keymap().preset==='tmux')
  // Wait for the App's resolved map, not just the stored preset.
  await page.waitForFunction(()=>document.getElementById('demokeymaphint').textContent.includes('Ctrl+B'))
  const before=await frame.locator('.pane-stack').count()
  await frame.locator('.terminal-pane').first().click({position:{x:240,y:150}})
  await page.keyboard.press('Control+b'); await page.keyboard.press('Shift+5')
  await page.waitForTimeout(700)
  const after=await frame.locator('.pane-stack').count()
  assert.equal(after, before + 1, 'tmux split must work in the embedded frame')
  console.log(JSON.stringify({keyboard:{before,after,hint:await page.locator('#demokeymaphint').innerText()}, frameText:(await frame.locator('body').innerText()).slice(0,3500)}))
  await page.goto(`${base}/demo/?deterministic=1&scenario=land`)
  await page.waitForFunction(()=>window.__demoDirector?.snapshot().index===2,{},{timeout:30000})
  await page.getByRole('button',{name:'Pause walkthrough',exact:true}).click()
  const held=await page.evaluate(()=>window.__demoDirector.snapshot())
  await page.waitForTimeout(1500)
  assert.equal((await page.evaluate(()=>window.__demoDirector.snapshot())).index,held.index)
  await page.getByRole('button',{name:'Next',exact:true}).click()
  await page.waitForFunction(index=>window.__demoDirector.snapshot().index===index+1,held.index)
  assert.equal(await page.evaluate(()=>window.__demoDirector.snapshot().held),true)
  await page.screenshot({path:'.tmp/review/demo-land.png'})
  console.log('pause and manual stepping passed')
  await page.goto(`${base}/demo/?deterministic=1&scenario=preview`)
  await page.waitForFunction(()=>window.__demoDirector?.snapshot().running === true)
  await page.getByRole('button',{name:'Pause walkthrough',exact:true}).click()
  for (let step=1;step<7;step++) {
    await page.waitForFunction(()=>!window.__demoDirector.snapshot().acting)
    await page.getByRole('button',{name:'Next',exact:true}).click()
    await page.waitForFunction(previous=>window.__demoDirector.snapshot().index===previous+1,step)
  }
  await page.locator('.preview-pane:visible').waitFor({ timeout: 15000 })
  assert.equal(await page.locator('.preview-pane:visible').count(),1,'preview must remain visible beside the agent')
  assert.equal(await page.locator('.terminal-pane:visible').count(),1,'agent must remain visible beside the preview')
  await page.screenshot({path:'.tmp/review/demo-preview.png'})
  await page.setViewportSize({width:390,height:844})
  await page.goto(base)
  await page.screenshot({path:'.tmp/review/home-phone.png'})
  await writeFile('.tmp/review/errors.json',JSON.stringify(failures,null,2))
  assert.deepEqual(failures, [], 'no browser render errors')
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390, 'no phone overflow')
  console.log('embedded keyboard, pause, step and mobile checks passed')
} finally { await browser.close(); server.closeAllConnections(); await new Promise(resolve=>server.close(resolve)) }
