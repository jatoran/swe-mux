import { expect, test } from 'playwright/test'

test('first Git Map open offers the explicit portable or whole-directory choice',async({page})=>{
  await page.goto('/git-setup-harness.html')
  const setup=page.locator('.git-swe-mux-setup')
  await expect(setup).toContainText('Portable Project settings')
  await setup.getByRole('button',{name:'Ignore all .swe-mux files'}).click()
  await expect(setup).toHaveCount(0)
  expect(await page.evaluate(()=>Reflect.get(globalThis,'__setupDecisions'))).toEqual([
    {project_id:'project',decision:'ignore_all'},
  ])
})

test('tracked swe-mux files disable the misleading ignore action',async({page})=>{
  await page.goto('/git-setup-harness.html?tracked=1')
  const setup=page.locator('.git-swe-mux-setup')
  await expect(setup).toContainText('already contains tracked files')
  await expect(setup.getByRole('button',{name:'Ignore all .swe-mux files'})).toHaveCount(0)
})

test('repository initialization carries the unchecked or explicit whole-directory choice',async({page})=>{
  await page.goto('/git-setup-harness.html?notrepo=1')
  const choice=page.getByLabel('Ignore all .swe-mux/ Project files')
  await expect(choice).not.toBeChecked()
  await choice.check()
  await page.getByRole('button',{name:'Initialize repository'}).click()
  expect(await page.evaluate(()=>Reflect.get(globalThis,'__setupDecisions'))).toEqual([
    {project_id:'project',ignore_swe_mux:true},
  ])
})

test('Never ask again can be restored by the Settings-owned install switch',async({page})=>{
  await page.goto('/git-setup-harness.html')
  const setup=page.locator('.git-swe-mux-setup')
  await setup.getByRole('button',{name:'Never ask again'}).click()
  await expect(setup).toHaveCount(0)
  await page.evaluate(()=>Reflect.get(globalThis,'__enableSetupPrompt')())
  await expect(setup).toContainText('Choose how Git treats swe-mux files')
})
