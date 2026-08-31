import { expect, test } from 'playwright/test'

test('Plugins is a compact management list with focused alphabetical Project targeting',async({page})=>{
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

  const target=page.getByRole('combobox',{name:'Test Project',exact:true})
  await expect(target).toHaveValue('p2')
  await expect(target.locator('option')).toHaveText(['Alpha','Beta','Zulu'])

  await switchboard.getByRole('button',{name:'Expand Session Switchboard',exact:true}).click()
  await expect(switchboard.locator('.plugin-card-details')).toBeVisible()
  await expect(switchboard).toContainText('C:/plugins/switchboard')
  await expect(switchboard.getByRole('button',{name:'Session switchboard',exact:true})).toBeVisible()
})
