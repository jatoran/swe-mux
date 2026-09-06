import { expect, test, type Page } from 'playwright/test'

type Request={path:string;method:string;body:Record<string,unknown>}
async function server(page:Page,options:{scripts?:boolean;failCreation?:boolean;failConfig?:boolean;failScripts?:boolean;delayCreate?:boolean}={}){
  const requests:Request[]=[]
  let failed=false
  let finishCreate:()=>void=()=>{}
  const created=new Promise<void>(resolve=>{finishCreate=resolve})
  await page.route('**/api/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname
    const body=(request.postDataJSON()||{}) as Record<string,unknown>
    requests.push({path,method:request.method(),body})
    let value:unknown={};let status=200
    if(path==='/api/config'){
      if(options.failConfig&&!failed){failed=true;status=503;value={error:'Settings unavailable'}}
      else value={llm_provider:'custom',custom_llm_base_url:'https://gateway.example/v1',automation_project_defaults:{doc_debt:true,scan_timeline:true},new_project_parent:'D:/workspaces',project_init_scripts:options.scripts?[{id:'git',label:'Initialize Git',command:'git init',default_enabled:true},{id:'optional',label:'Optional command',command:'echo optional',default_enabled:false}]:[]}
    }else if(path==='/api/projects'){
      if(options.delayCreate)await created
      if(options.failCreation&&!failed){failed=true;status=422;value={error:'Invalid project',fields:{root:'Folder unavailable'}}}
      else{status=201;value={id:'p1',...body}}
    }else if(path==='/api/projects/p1/init-scripts/run')value={errors:options.failScripts?[{script:'git',error:'Git could not start'}]:[]}
    else{status=500;value={error:`Unexpected request: ${path}`}}
    await route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)})
  })
  return {requests,finishCreate}
}
async function fillExisting(page:Page){
  await page.getByLabel('Name',{exact:true}).fill('Horizon')
  await page.getByLabel('Folder',{exact:true}).fill('D:/projects/horizon')
}

test('a custom gateway needs no provider check or project-specific settings to create',async({page})=>{
  const app=await server(page)
  await page.goto('/project-create-harness.html')
  await expect(page.getByText('Global defaults selected',{exact:true})).toBeVisible()
  await expect(page.getByLabel('Name',{exact:true})).toBeFocused()
  await expect(page.getByRole('checkbox')).toHaveCount(0)
  await expect(page.getByRole('button',{name:/Set up.*provider/i})).toHaveCount(0)
  await expect(page.getByRole('button',{name:'Project group'})).toHaveCount(0)
  await fillExisting(page)
  await page.getByRole('button',{name:'Create project',exact:true}).click()
  await expect(page.getByRole('heading',{name:'Workspace: Horizon'})).toBeVisible()
  expect(app.requests).toEqual([
    {path:'/api/config',method:'GET',body:{}},
    {path:'/api/projects',method:'POST',body:{name:'Horizon',root:'D:/projects/horizon',group_id:null,create_missing:false}},
  ])
})

test('new folders use the global parent and optional group without writing policy',async({page})=>{
  const app=await server(page)
  await page.goto('/project-create-harness.html?groups')
  await page.getByRole('tab',{name:'New folder',exact:true}).click()
  await page.getByLabel('Name',{exact:true}).fill('Horizon Web')
  await expect(page.getByLabel('Parent folder',{exact:true})).toHaveValue('D:/workspaces')
  await expect(page.getByLabel('New folder name',{exact:true})).toHaveValue('Horizon-Web')
  await page.getByRole('button',{name:'Project group',exact:true}).click()
  await page.getByRole('option',{name:'Work',exact:true}).click()
  await page.getByRole('button',{name:'Create project',exact:true}).click()
  await expect(page.getByRole('heading',{name:'Workspace: Horizon Web'})).toBeVisible()
  expect(app.requests.at(-1)?.body).toEqual({name:'Horizon Web',root:'D:/workspaces/Horizon-Web',group_id:'g1',create_missing:true})
  expect(app.requests).toHaveLength(2)
})

test('setup commands follow global defaults and expose a read-only summary',async({page})=>{
  const app=await server(page,{scripts:true})
  await page.goto('/project-create-harness.html')
  await page.getByText('Global setup commands (1)',{exact:true}).click()
  await expect(page.getByText('git init',{exact:true})).toBeVisible()
  await expect(page.getByRole('checkbox')).toHaveCount(0)
  await expect(page.getByText('Optional command',{exact:true})).toHaveCount(0)
  await fillExisting(page)
  await page.getByRole('button',{name:'Create project',exact:true}).click()
  await expect.poll(()=>app.requests.length).toBe(3)
  expect(app.requests[2]).toEqual({path:'/api/projects/p1/init-scripts/run',method:'POST',body:{script_ids:['git']}})
})

test('registration errors preserve the form and allow retry',async({page})=>{
  const app=await server(page,{failCreation:true})
  await page.goto('/project-create-harness.html');await fillExisting(page)
  await page.getByRole('button',{name:'Create project',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('Folder unavailable')
  await expect(page.getByLabel('Name',{exact:true})).toHaveValue('Horizon')
  await page.getByRole('button',{name:'Create project',exact:true}).click()
  await expect(page.getByRole('heading',{name:'Workspace: Horizon'})).toBeVisible()
  expect(app.requests.filter(request=>request.method==='POST')).toHaveLength(2)
})

test('unknown global setup defaults are retried before any project is created',async({page})=>{
  const app=await server(page,{failConfig:true})
  await page.goto('/project-create-harness.html');await fillExisting(page)
  await expect(page.getByRole('alert')).toContainText('Could not load global defaults')
  await expect(page.getByRole('button',{name:'Create project',exact:true})).toBeDisabled()
  await page.getByRole('button',{name:'Retry',exact:true}).click()
  await page.getByRole('button',{name:'Create project',exact:true}).click()
  await expect(page.getByRole('heading',{name:'Workspace: Horizon'})).toBeVisible()
  expect(app.requests.filter(request=>request.method==='POST')).toHaveLength(1)
})

test('a failed default setup command does not undo registration',async({page})=>{
  const app=await server(page,{scripts:true,failScripts:true})
  await page.goto('/project-create-harness.html');await fillExisting(page)
  await page.getByRole('button',{name:'Create project',exact:true}).click()
  await expect(page.getByRole('heading',{name:'Workspace: Horizon'})).toBeVisible()
  await expect(page.getByRole('alert')).toContainText('Git could not start')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(app.requests.filter(request=>request.path==='/api/projects')).toHaveLength(1)
})

test('repeated submission while creation is pending registers only once',async({page})=>{
  const app=await server(page,{delayCreate:true})
  await page.goto('/project-create-harness.html');await fillExisting(page)
  await page.getByRole('button',{name:'Create project',exact:true}).click()
  await expect(page.getByRole('button',{name:'Creating…',exact:true})).toBeDisabled()
  await page.locator('form').evaluate(form=>(form as HTMLFormElement).requestSubmit())
  app.finishCreate()
  await expect(page.getByRole('heading',{name:'Workspace: Horizon'})).toBeVisible()
  expect(app.requests.filter(request=>request.path==='/api/projects')).toHaveLength(1)
})

test('the compact form fits on a phone without configuration lists',async({page})=>{
  await page.setViewportSize({width:390,height:844})
  await server(page)
  await page.goto('/project-create-harness.html')
  await expect(page.getByText('Global defaults selected',{exact:true})).toBeVisible()
  const box=await page.locator('.project-create-dialog').boundingBox()
  expect(box!.x).toBeGreaterThanOrEqual(0);expect(box!.x+box!.width).toBeLessThanOrEqual(390)
  await expect(page.locator('.project-create-dialog details')).toHaveCount(0)
  await page.screenshot({path:'../.trash/project-create-mobile.png'})
})

test('Escape dismisses creation without registering a Project',async({page})=>{
  const app=await server(page)
  await page.goto('/project-create-harness.html')
  await expect(page.getByLabel('Name',{exact:true})).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(app.requests.some(request=>request.method!=='GET')).toBe(false)
})
