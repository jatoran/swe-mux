import { expect, test } from 'playwright/test'

test('plugin popups render on mobile and can persist as a Project tab',async({page})=>{
  await page.setViewportSize({width:390,height:844})
  await page.goto('/plugin-popup-harness.html')

  await expect(page.getByRole('dialog',{name:'Plugin popup',exact:true})).toBeVisible()
  await expect(page.getByText('Healthy worktrees',{exact:true})).toBeVisible()
  const dock=page.getByRole('button',{name:'Keep as Project tab',exact:true})
  await expect(dock).toBeVisible()
  await dock.click()
  await expect(page.getByRole('status')).toHaveText('Docked as a Project tab')
})
