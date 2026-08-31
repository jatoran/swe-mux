import { expect, test } from 'playwright/test'

test('Plugins is a compact global manager with explicit refresh and development discovery',async({page})=>{
  await page.setViewportSize({width:1280,height:900})
  await page.goto('/settings-harness.html')
  await page.getByRole('tab',{name:'Plugins',exact:true}).click()

  const cards=page.locator('.plugin-card')
  await expect(cards).toHaveCount(2)
  const switchboard=cards.filter({hasText:'Session Switchboard'})
  await expect(switchboard.getByRole('button',{name:'Disable',exact:true})).toBeVisible()
  await expect(switchboard.getByRole('button',{name:'Uninstall',exact:true})).toBeVisible()
  await expect(switchboard.locator('.plugin-card-details')).toHaveCount(0)
  expect((await switchboard.boundingBox())!.height).toBeLessThan(65)

  await expect(page.getByRole('combobox',{name:'Test Project',exact:true})).toHaveCount(0)
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'Check for updates',exact:true})).toBeVisible()
  await expect(page.getByRole('textbox',{name:'Development root',exact:true})).toHaveValue('C:/Users/Test/swe-mux-plugins')
  await expect(page.getByText('Local Tool',{exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'Link',exact:true})).toBeVisible()

  await switchboard.getByRole('button',{name:'Expand Session Switchboard',exact:true}).click()
  await expect(switchboard.locator('.plugin-card-details')).toBeVisible()
  await expect(switchboard).toContainText('C:/plugins/switchboard')
  await expect(switchboard).toContainText("Launch Project tools from that Project's Run menu")
  await expect(switchboard.getByRole('button',{name:'Restart panes',exact:true})).toBeVisible()

  const health=cards.filter({hasText:'Worktree Health'})
  await health.getByRole('button',{name:'Expand Worktree Health',exact:true}).click()
  await expect(health).toContainText('Review update')
  await expect(health).toContainText('sessions.read')
  await expect(health.getByRole('button',{name:'Approve update',exact:true})).toBeVisible()
})

test('plugin management remains usable without horizontal overflow on mobile',async({page})=>{
  await page.setViewportSize({width:390,height:844})
  await page.goto('/settings-harness.html')
  await page.getByRole('button',{name:'Settings sections',exact:true}).click()
  await page.getByRole('tab',{name:'Plugins',exact:true}).click()

  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'Check for updates',exact:true})).toBeVisible()
  await expect(page.getByRole('textbox',{name:'Development root',exact:true})).toBeVisible()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true)
})
