import { expect, test, type Page } from 'playwright/test'
import { SETTINGS_CONFIG_FIXTURE } from './settingsConfigFixture'
import { HARNESS_REGISTRY_SEED } from '../../src/harnessRegistrySeed'
import type { OnboardingState } from '../../src/onboarding'

async function daemon(page:Page,options:{existing?:boolean;startupFailure?:boolean;badModels?:boolean;desktop?:boolean}={}){
  let state:OnboardingState={version:1,revision:0,step:options.existing?'existing':'experience',status:'active',hidden:false,tour_status:'pending',tour_step:'welcome',dismissed:[],completed:[],draft:{}}
  const config:Record<string,unknown>={...SETTINGS_CONFIG_FIXTURE,experience_tier:'',harness_setup_complete:false,openrouter_cheap_model:'',openrouter_standard_model:''}
  const writes:{path:string;body:Record<string,unknown>}[]=[]
  const projects:{root:string;name:string}[]=[]
  let failed=false
  let verified=false
  let stored=false
  const slots:Record<string,{present:boolean;path:string}>={desktop:{present:false,path:'desktop'},'start-menu':{present:false,path:'start-menu'},startup:{present:false,path:'startup'}}
  const catalog=[{id:config.scan_timeline_model as string,name:'Fast structured model',prompt_price:0.00000008,completion_price:0.0000003},{id:config.assistant_model as string,name:'Standard tool model',prompt_price:0.00000125,completion_price:0.00001}]
  const provider=()=>{
    const custom=config.llm_provider==='custom'
    const capabilities={catalog:custom?'none':'annotated',reports_cost:!custom,reports_cache:false}
    const readiness={ready:verified,provider:custom?'custom':'openrouter',code:verified?'ready':'no_key',reason:verified?'Ready':'Set up a provider.'}
    return {provider:readiness.provider,llm:readiness,secret:{configured:stored,source:'stored'},models:{models:custom?[]:catalog},providers:['openrouter','custom'].map(id=>({id,active:config.llm_provider===id,secret:{configured:stored,source:'stored'},verification:{verified,capabilities},readiness}))}
  }
  await page.route('**/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname
    const body=(request.postDataJSON()||{}) as Record<string,unknown>
    if(request.method()!=='GET')writes.push({path,body})
    let result:unknown={};let status=200
    if(path==='/api/onboarding'){
      if(request.method()==='GET'&&options.startupFailure&&!failed){failed=true;status=503;result={error:'Daemon is starting'}}
      else if(request.method()==='PATCH'){
        if(body.revision!==state.revision){status=409;result={error:'Setup changed on another device.',state}}
        else {const {revision,action,...patch}=body;void revision;state={...state,...patch,revision:state.revision+1} as OnboardingState;if(action==='fresh')state={...state,step:'experience',draft:{}};if(action==='reuse')state={...state,step:'finish'};result=state}
      }else result=state
    }else if(path==='/api/config'){
      if(request.method()==='PATCH')Object.assign(config,body)
      result=config
    }else if(path==='/api/harnesses')result={...HARNESS_REGISTRY_SEED,harnesses:HARNESS_REGISTRY_SEED.harnesses.map(harness=>({...harness,installed:['claude','codex'].includes(harness.name)}))}
    else if(path==='/api/keybindings')result={presets:[{id:'swemux',title:'swe-mux',description:'Standard shortcuts',warning:''}]}
    else if(path==='/api/experience-tiers')result={tiers:{terminal:{automation_enabled:false},deterministic:{automation_enabled:false},automations:{automation_enabled:true}},autonomy:{supervised:{}},overridable:['automation_enabled']}
    else if(path==='/api/experience-tier'){config.experience_tier=body.tier;config.automation_enabled=body.tier==='automations';result={restart_required:[]}}
    else if(path==='/api/provider-accounts')result={providers:['claude','codex'],current:{claude:{state:'external',email:'tester@example.test'},codex:{state:'saved'}},accounts:[]}
    else if(path.endsWith('/capture'))result={providers:['claude','codex'],current:{claude:{state:'saved',email:'tester@example.test'},codex:{state:'saved'}},accounts:[]}
    else if(path==='/api/projects'){if(request.method()==='POST')projects.push(body as {root:string;name:string});result=request.method()==='POST'?body:projects}
    else if(path==='/api/onboarding/projects')result={items:[{root:'D:/recent-project',name:'Recent project',sessions:8,last_activity:100,harnesses:['claude','codex'],available:true},{root:'D:/missing-project',name:'Missing project',sessions:1,last_activity:10,harnesses:['claude'],available:false}],limited:false}
    else if(path==='/api/desktop/integration')result={supported:options.desktop??true,shortcuts:{slots},shell:{importable:true,install_kind:'uv-tool'}}
    else if(path==='/api/desktop/integration/shortcuts'){for(const slot of body.slots as string[])slots[slot].present=true}
    else if(path==='/api/automation/provider')result=provider()
    else if(path==='/api/automation/provider/key'){stored=true;result={ok:true}}
    else if(path==='/api/automation/provider/verify'){verified=true;result={ok:true,...provider()}}
    else if(path==='/api/onboarding/models/verify'){if(options.badModels){status=422;result={error:'The assistant model did not return a tool call.'}}else result={ok:true}}
    await route.fulfill({status,contentType:'application/json',body:JSON.stringify(result)})
  })
  return {writes,config,projects,state:()=>state}
}

test('delayed startup recovers into setup; deferral survives reload and quests stay out of the workspace',async({page})=>{
  const app=await daemon(page,{startupFailure:true})
  await page.goto('/onboarding-harness.html')
  await expect(page.getByRole('alert')).toContainText('Daemon is starting')
  await expect(page.getByText('SET UP::EXPERIENCE')).toBeVisible()
  await expect(page.locator('main .quest-log')).toHaveCount(0)
  await page.getByRole('radio',{name:/Automations/}).check()
  await page.getByRole('button',{name:'Continue later',exact:true}).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.reload()
  await page.getByRole('button',{name:/Getting started/}).first().click()
  await page.getByRole('button',{name:'Continue setup',exact:true}).click()
  await expect(page.getByRole('radio',{name:/Automations/})).toBeChecked()
  expect(app.config.automation_enabled).toBe(false)
})

test('complete setup saves the current account, imports selected recent projects and hands off to the UI tour',async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message))
  const app=await daemon(page)
  await page.goto('/onboarding-harness.html')
  await page.getByRole('button',{name:'Continue',exact:true}).click()
  await expect(page.getByText('SET UP::AGENTS')).toBeVisible()
  await page.getByRole('button',{name:'Save current login',exact:true}).click()
  await expect(page.getByText('Already saved',{exact:false})).toHaveCount(2)
  await page.getByRole('button',{name:'Enable selected'}).click()
  await page.getByRole('button',{name:'Find recent project folders'}).click()
  await page.getByRole('checkbox',{name:/Recent project/}).check()
  await expect(page.getByRole('checkbox',{name:/Missing project/})).toBeDisabled()
  await page.getByRole('button',{name:'Add selected folders (1)'}).click()
  await expect(page.getByRole('status')).toContainText('1 Project ready')
  await page.getByRole('button',{name:'Continue',exact:true}).click()
  await expect(page.getByRole('checkbox',{name:'Desktop shortcut Optional'})).not.toBeChecked()
  await page.getByRole('button',{name:'Create selected shortcuts'}).click()
  await page.getByRole('button',{name:'Continue',exact:true}).click()
  await page.getByRole('button',{name:'Start the UI tour'}).click()
  await expect(page.locator('.tutorial-card')).toBeVisible()
  expect(app.state().status).toBe('complete')
  expect(app.projects).toHaveLength(1)
  expect(app.config.default_harness).toBe('claude')
  expect(app.config.default_backend).toBe('claude')
  expect(errors).toEqual([])
})

test('Automations immediately requires models and a failed model check cannot activate it',async({page})=>{
  const app=await daemon(page,{badModels:true})
  await page.goto('/onboarding-harness.html')
  await page.getByRole('radio',{name:/Automations/}).check()
  await page.getByRole('button',{name:'Continue',exact:true}).click()
  await expect(page.getByRole('heading',{name:'Set up models for Automations'})).toBeVisible()
  expect(app.writes.filter(write=>write.path==='/api/experience-tier')).toHaveLength(0)
  await page.getByLabel('API key',{exact:true}).fill('test-key')
  await page.getByRole('button',{name:'Save and test endpoint'}).click()
  await page.getByRole('button',{name:'Approve models and enable Automations'}).click()
  await expect(page.getByRole('alert')).toContainText('did not return a tool call')
  expect(app.config.automation_enabled).toBe(false)
  await page.getByRole('button',{name:'Continue with Deterministic'}).click()
  await expect(page.getByText('SET UP::AGENTS')).toBeVisible()
  expect(app.config.experience_tier).toBe('deterministic')
  expect(JSON.stringify(app.state())).not.toContain('test-key')
})

test('keyless local endpoint completes setup and mobile controls stay inside the viewport',async({page})=>{
  await page.setViewportSize({width:390,height:844})
  const app=await daemon(page)
  await page.goto('/onboarding-harness.html')
  await page.screenshot({path:'../.trash/onboarding-mobile-experience.png'})
  await page.getByRole('radio',{name:/Automations/}).check()
  await page.getByRole('button',{name:'Continue',exact:true}).click()
  await page.getByLabel('Model provider',{exact:true}).selectOption('custom')
  await page.getByLabel('Base URL',{exact:true}).fill('http://127.0.0.1:11434/v1')
  await page.getByLabel('Single model (when there is no catalog)').fill('local-model')
  await page.getByRole('button',{name:'Save and test endpoint'}).click()
  await expect(page.getByRole('button',{name:'Approve models and enable Automations'})).toBeVisible()
  await page.screenshot({path:'../.trash/onboarding-mobile-models.png'})
  const box=await page.locator('.harness-setup').boundingBox()
  expect(box!.x).toBeGreaterThanOrEqual(0);expect(box!.x+box!.width).toBeLessThanOrEqual(390)
  await page.getByRole('button',{name:'Approve models and enable Automations'}).click()
  await expect(page.getByText('SET UP::AGENTS')).toBeVisible()
  expect(app.writes.filter(write=>write.path==='/api/automation/provider/key')).toHaveLength(0)
  expect(app.config.automation_enabled).toBe(true)
})

test('retained settings can be reused and dismissed learning tasks restored',async({page})=>{
  const app=await daemon(page,{existing:true})
  await page.goto('/onboarding-harness.html')
  await expect(page.getByRole('heading',{name:'Use your existing settings?'})).toBeVisible()
  await page.getByRole('button',{name:'Use existing settings',exact:true}).click()
  await page.getByRole('button',{name:'Explore at my own pace'}).click()
  await page.getByRole('button',{name:/Getting started/}).first().click()
  await page.getByRole('button',{name:'Dismiss Connect your phone',exact:true}).click()
  await expect(page.getByRole('button',{name:'Dismiss Connect your phone',exact:true})).toHaveCount(0)
  await page.getByRole('button',{name:'Restore dismissed steps'}).click()
  await expect(page.getByRole('button',{name:'Dismiss Connect your phone',exact:true})).toBeVisible()
  await page.screenshot({path:'../.trash/onboarding-desktop-sidebar.png'})
  expect(app.state().tour_status).toBe('deferred')
})
