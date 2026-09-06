import { expect, test, type Page } from 'playwright/test'
import { SETTINGS_CONFIG_FIXTURE } from './settingsConfigFixture'
import { HARNESS_REGISTRY_SEED } from '../../src/harnessRegistrySeed'
import type { OnboardingState, SetupDraft, SetupStep } from '../../src/onboarding'

type Options={existing?:boolean;startupFailure?:boolean;badModels?:boolean;harnessDelayMs?:number;scanDelayMs?:number;noAgents?:boolean;step?:SetupStep;draft?:SetupDraft;voiceReady?:boolean;providerReady?:boolean;addFailure?:boolean}
async function daemon(page:Page,options:Options={}){
  let state:OnboardingState={version:1,revision:0,step:options.step||(options.existing?'existing':'experience'),status:'active',hidden:false,tour_status:'pending',tour_step:'welcome',dismissed:[],completed:[],draft:options.draft||{}}
  const config:Record<string,unknown>={...SETTINGS_CONFIG_FIXTURE,revision:1,experience_tier:'',harness_setup_complete:false,openrouter_cheap_model:'',openrouter_standard_model:''}
  const writes:{path:string;body:Record<string,unknown>}[]=[]
  const scans:string[]=[]
  const projects:{id:string;root:string;name:string}[]=[]
  let failed=false,verified=!!options.providerReady,rolesVerified=!!options.providerReady,stored=false,saved=false,addFailed=false
  let keymap='swemux'
  const catalog=[{id:config.scan_timeline_model as string,name:'Fast structured model',prompt_price:0.00000008,completion_price:0.0000003},{id:config.assistant_model as string,name:'Standard tool model',prompt_price:0.00000125,completion_price:0.00001}]
  const provider=()=>{
    const custom=config.llm_provider==='custom'
    const capabilities={catalog:custom?'none':'annotated',reports_cost:!custom,reports_cache:false}
    const readiness={ready:verified,provider:custom?'custom':'openrouter',code:verified?'ready':'no_key',reason:verified?'Ready':'Set up a provider.'}
    return {provider:readiness.provider,llm:readiness,activation:{...readiness,ready:rolesVerified},secret:{configured:stored,source:'stored'},models:{models:custom?[]:catalog},providers:['openrouter','custom'].map(id=>({id,active:config.llm_provider===id,secret:{configured:stored,source:'stored'},verification:{verified,capabilities},readiness}))}
  }
  const slots:Record<string,{present:boolean;path:string}>={'start-menu':{present:false,path:'menu'},desktop:{present:false,path:'desktop'},startup:{present:false,path:'startup'}}
  await page.route('**/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname
    const body=(request.postDataJSON()||{}) as Record<string,unknown>
    if(request.method()!=='GET')writes.push({path,body})
    let result:unknown={};let status=200
    if(path==='/api/onboarding'){
      if(request.method()==='GET'&&options.startupFailure&&!failed){failed=true;status=503;result={error:'Daemon is starting'}}
      else if(request.method()==='PATCH'){
        if(body.revision!==state.revision){status=409;result={error:'Setup changed on another device.',state}}
        else{const {revision,action,...patch}=body;void revision;state={...state,...patch,revision:state.revision+1} as OnboardingState;if(action==='fresh')state={...state,step:'experience',draft:{}};if(action==='reuse')state={...state,step:'finish'};result=state}
      }else result=state
    }else if(path==='/api/config'){
      if(request.method()==='PATCH'){Object.assign(config,body);config.revision=Number(config.revision)+1}
      result=config
    }else if(path==='/api/harnesses'){
      if(options.harnessDelayMs)await new Promise(resolve=>setTimeout(resolve,options.harnessDelayMs))
      result={...HARNESS_REGISTRY_SEED,harnesses:HARNESS_REGISTRY_SEED.harnesses.map(harness=>({...harness,installed:!options.noAgents&&['claude','codex'].includes(harness.name),resolved_path:!options.noAgents&&['claude','codex'].includes(harness.name)?harness.name:null}))}
    }else if(path==='/api/keybindings'){if(request.method()==='PUT')keymap='custom';result={preset:keymap,presets:[{id:'swemux',title:'swe-mux',description:'Standard shortcuts',warning:''},{id:'tmux',title:'tmux',description:'Prefix shortcuts',warning:'Reserves Ctrl+B.'}]}}
    else if(path==='/api/keymap-preset'){keymap=String(body.preset)}
    else if(path==='/api/experience-tiers')result={tiers:{terminal:{automation_enabled:false,scan_timeline_enabled:false},deterministic:{automation_enabled:false,scan_timeline_enabled:false,agent_messaging_enabled:true},automations:{automation_enabled:true,scan_timeline_enabled:true,agent_messaging_enabled:true}},autonomy:{supervised:{auto_delivery_enabled:false},assisted:{auto_delivery_enabled:true},autonomous:{auto_delivery_enabled:true}},overridable:['automation_enabled','scan_timeline_enabled','agent_messaging_enabled']}
    else if(path==='/api/experience-tier'){config.experience_tier=body.tier;config.automation_enabled=body.tier==='automations';config.harness_mcp_enabled={};result={restart_required:[]}}
    else if(path==='/api/provider-accounts'||path.endsWith('/capture')){if(path.endsWith('/capture'))saved=true;result={providers:['claude','codex'],current:{claude:{state:saved?'saved':'external',email:'tester@example.test'},codex:{state:'saved'}},accounts:[]}}
    else if(path==='/api/projects'){
      if(request.method()==='POST'){
        if(options.addFailure&&!addFailed&&projects.length===1){status=422;result={error:'Folder unavailable'};addFailed=true}
        else{const project={id:`project-${projects.length+1}`,root:String(body.root),name:String(body.name||'My project')};projects.push(project);result=project}
      }else result=projects
    }else if(path==='/api/onboarding/projects'){
      const harness=url.searchParams.get('harnesses')||'';scans.push(harness)
      if(options.scanDelayMs&&harness==='codex')await new Promise(resolve=>setTimeout(resolve,options.scanDelayMs))
      result={items:harness==='claude'?[{root:'D:/recent-project',name:'Recent project',sessions:8,last_activity:100,harnesses:['claude'],available:true},{root:'D:/missing-project',name:'Missing project',sessions:1,last_activity:10,harnesses:['claude'],available:false}]:[{root:'D:/second-project',name:'Second project',sessions:3,last_activity:80,harnesses:['codex'],available:true}],limited:false}
    }else if(path==='/api/sessions')result={id:'first-session',...body}
    else if(path==='/api/desktop/integration')result={supported:true,shortcuts:{slots},shell:{importable:true,install_kind:'uv-tool'}}
    else if(path==='/api/desktop/integration/shortcuts'){for(const slot of body.slots as string[])slots[slot].present=true}
    else if(path==='/api/automation/provider')result=provider()
    else if(path==='/api/automation/provider/key'){stored=true;result={ok:true}}
    else if(path==='/api/automation/provider/verify'){verified=true;result={ok:true,...provider()}}
    else if(path==='/api/onboarding/models/configure'){config.openrouter_cheap_model=body.cheap;config.openrouter_standard_model=body.regular;config.revision=Number(config.revision)+1;result=config}
    else if(path==='/api/onboarding/models/verify'){if(options.badModels){status=422;result={error:'The assistant model did not return a tool call.'}}else{rolesVerified=true;result={ok:true}}}
    else if(path==='/api/onboarding/features/activate'){
      if(!rolesVerified){status=409;result={error:'Verify model roles first.'}}
      else{if((body.features as string[]).includes('automations')){config.automation_enabled=true;config.scan_timeline_enabled=true;Object.assign(config,body.overrides||{})}result={ok:true}}
    }else if(path==='/api/voice')result={providers:{sapi:{available:true},kokoro:{available:!!options.voiceReady}},engine:config.tts_engine,stt_engine:config.stt_engine,stt_available:!!options.voiceReady,voice_runtime:{supported:true,status:'ready'},kokoro_model:{status:'not_downloaded',total_bytes:100,downloaded_bytes:0,voices:[]},stt_models:[{model:'turbo',status:options.voiceReady?'ready':'not_downloaded',backend_installed:true}]}
    else if(path==='/api/voice/speak')result={id:'test-clip'}
    else if(path==='/api/voice/models/kokoro')result={status:'not_downloaded',total_bytes:100,downloaded_bytes:0,voices:[]}
    else if(path==='/api/voice/models/runtime')result={supported:true,status:'ready',total_bytes:100,downloaded_bytes:100}
    else if(path==='/api/voice/models/whisper')result={models:[{model:'turbo',status:options.voiceReady?'ready':'not_downloaded',backend_installed:true,size_hint:'1 GB'}]}
    await route.fulfill({status,contentType:'application/json',body:JSON.stringify(result)})
  })
  return {writes,config,projects,scans,state:()=>state,keymap:()=>keymap}
}
const next=(page:Page)=>page.getByRole('button',{name:'Continue',exact:true}).click()
async function core(page:Page,smart=false){
  await page.goto('/onboarding-harness.html')
  if(smart)await page.getByRole('radio',{name:/Smart workspace/}).check()
  await next(page)
  await expect(page.getByRole('checkbox',{name:/Claude Code Detected/})).toBeChecked()
  await next(page)
  await expect(page.getByRole('heading',{name:'Where do you want to work?'})).toBeVisible()
}
async function optionalModels(page:Page){
  await core(page,true)
  await page.getByRole('button',{name:'Add a Project later'}).click()
  await page.getByRole('button',{name:'Continue optional setup'}).click()
  await next(page)
  await expect(page.getByRole('heading',{name:'Connect models for smart features.'})).toBeVisible()
}

test('startup retry and deferred experience choice survive reload',async({page})=>{
  const app=await daemon(page,{startupFailure:true})
  await page.goto('/onboarding-harness.html')
  await expect(page.getByRole('alert')).toContainText('Daemon is starting')
  await page.getByRole('radio',{name:/Smart workspace/}).check()
  await page.getByRole('button',{name:'Continue later',exact:true}).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.reload()
  await page.getByRole('region',{name:'Getting started',exact:true}).getByRole('button',{name:/^[▾▸] Getting started/}).click()
  await page.getByRole('button',{name:'Continue setup',exact:true}).click()
  await expect(page.getByRole('radio',{name:/Smart workspace/})).toBeChecked()
  expect(app.config.automation_enabled).toBe(false)
})

test('detection remains unknown on fast Continue; detected agents then seed normally',async({page})=>{
  const app=await daemon(page,{harnessDelayMs:1300})
  await page.goto('/onboarding-harness.html')
  await next(page)
  await expect(page.getByRole('button',{name:'Continue',exact:true})).toBeDisabled()
  expect(app.state().draft.harnesses).toBeUndefined()
  await expect(page.getByRole('checkbox',{name:/Claude Code Detected/})).toBeChecked()
  await next(page)
  expect((app.config.harness_enabled as Record<string,boolean>).claude).toBe(true)
  expect(app.config.default_backend).toBe('claude')
})

test('explicit Shell-only selection before detection stays empty and can proceed',async({page})=>{
  const app=await daemon(page,{harnessDelayMs:1500})
  await page.goto('/onboarding-harness.html');await next(page)
  await page.getByRole('button',{name:'Use Shell only',exact:true}).click();await next(page)
  await expect(page.getByRole('heading',{name:'Where do you want to work?'})).toBeVisible()
  expect(app.state().draft.harnesses).toEqual({})
  expect(app.config.default_backend).toBe('shell')
  expect((app.config.harness_enabled as Record<string,boolean>).claude).toBe(false)
  expect(app.scans).toEqual([])
})

test('background scans populate independently and filtering preserves multi-selection',async({page})=>{
  const app=await daemon(page,{scanDelayMs:1800})
  await core(page)
  await expect(page.getByRole('checkbox',{name:/Recent project/})).toBeVisible()
  await expect(page.getByText('Finding folders…')).toBeVisible()
  await page.getByRole('checkbox',{name:/Recent project/}).check()
  await expect(page.getByRole('checkbox',{name:/Second project/})).toBeVisible()
  await page.getByRole('searchbox',{name:'Filter project folders'}).fill('codex')
  await expect(page.getByRole('checkbox',{name:/Recent project/})).toHaveCount(0)
  await page.getByRole('checkbox',{name:/Second project/}).check()
  await page.getByRole('button',{name:'Add 2 and continue'}).click()
  await expect(page.getByRole('heading',{name:'Choose familiar shortcuts.'})).toBeVisible()
  expect(app.projects).toHaveLength(2)
  expect(app.scans.sort()).toEqual(['claude','codex'])
})

test('partial registration is preserved and retry does not duplicate successful folders',async({page})=>{
  const app=await daemon(page,{addFailure:true})
  await core(page)
  await page.getByRole('checkbox',{name:/Recent project/}).check()
  await page.getByRole('checkbox',{name:/Second project/}).check()
  await page.getByRole('button',{name:'Add 2 and continue'}).click()
  await expect(page.getByRole('alert')).toContainText('Folder unavailable')
  expect(app.projects).toHaveLength(1)
  await page.getByRole('button',{name:'Add 2 and continue'}).click()
  await expect(page.getByRole('heading',{name:'Choose familiar shortcuts.'})).toBeVisible()
  expect(app.projects).toHaveLength(2)
})

test('Just terminals adds a manual folder, chooses a keymap, and launches without optional setup',async({page})=>{
  const app=await daemon(page,{noAgents:true})
  await page.goto('/onboarding-harness.html')
  await page.getByRole('radio',{name:/Just terminals/}).check();await next(page)
  await page.getByRole('button',{name:'Use Shell only'}).click();await next(page)
  await page.getByRole('button',{name:'Enter a path'}).click()
  await page.getByLabel('Folder path',{exact:true}).fill('D:/manual-project')
  await page.getByRole('button',{name:'Add folder',exact:true}).click()
  await next(page)
  await page.getByRole('radio',{name:/tmux/}).check()
  await page.getByRole('button',{name:'Start working',exact:true}).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(app.writes.find(write=>write.path==='/api/sessions')?.body).toEqual({project_id:'project-1',backend:'shell'})
  expect(app.keymap()).toBe('tmux')
  expect(app.state().step).toBe('extras')
  expect(app.writes.some(write=>write.path.includes('/provider/'))).toBe(false)
})

test('Back and custom-keymap detour preserve choices without reapplying presets',async({page})=>{
  const app=await daemon(page)
  await core(page)
  await page.getByRole('button',{name:'Add a Project later'}).click()
  await page.getByRole('radio',{name:/tmux/}).check()
  await page.getByRole('button',{name:'Customize shortcuts…'}).click()
  await page.getByRole('button',{name:'Save customized shortcuts'}).click()
  await expect(page.getByRole('heading',{name:'Choose familiar shortcuts.'})).toBeVisible()
  await page.getByRole('button',{name:'Start working',exact:true}).click()
  expect(app.keymap()).toBe('custom')
  expect(app.writes.filter(write=>write.path==='/api/keymap-preset')).toHaveLength(1)
})

test('provider shows two read-only defaults; failed checks keep model features off',async({page})=>{
  const app=await daemon(page,{badModels:true})
  await optionalModels(page)
  await page.getByLabel('API key',{exact:true}).fill('test-key')
  await page.getByRole('button',{name:'Save and test connection'}).click()
  await expect(page.locator('.setup-model-row')).toHaveCount(2)
  await expect(page.locator('.model-picker')).toHaveCount(0)
  await expect(page.getByRole('button',{name:'Change cheap model'})).toBeVisible()
  await expect(page.getByRole('heading',{name:'Daily automation limit'})).toHaveCount(0)
  await page.getByRole('button',{name:'Verify models and continue'}).click()
  await expect(page.getByRole('alert')).toContainText('did not return a tool call')
  expect(app.config.automation_enabled).toBe(false)
  await page.getByRole('button',{name:'Set up later',exact:true}).click()
  await expect(page.getByRole('heading',{name:'Anything else before you start?'})).toBeVisible()
  expect(JSON.stringify(app.state())).not.toContain('test-key')
  expect(app.state().draft.model_features_pending).toBe(true)
})

test('keyless provider activation preserves fleet and feature choices',async({page})=>{
  const app=await daemon(page)
  await core(page,true)
  await page.getByRole('button',{name:'Add a Project later'}).click()
  await page.getByRole('button',{name:'Continue optional setup'}).click()
  await page.getByText('Limits and individual features',{exact:true}).click()
  await page.getByLabel('Agent fleet access').selectOption('none')
  await page.getByRole('checkbox',{name:'Agent messages',exact:true}).uncheck()
  await next(page)
  await page.getByLabel('Model provider',{exact:true}).selectOption('custom')
  await page.getByLabel('Base URL',{exact:true}).fill('http://127.0.0.1:11434/v1')
  await page.getByLabel('Single model (when there is no catalog)').fill('local-model')
  await page.getByRole('button',{name:'Save and test connection'}).click()
  await page.getByRole('button',{name:'Verify models and continue'}).click()
  await expect(page.getByRole('heading',{name:'Anything else before you start?'})).toBeVisible()
  expect(app.writes.filter(write=>write.path==='/api/experience-tier')).toHaveLength(1)
  expect(app.config.automation_enabled).toBe(true)
  expect(app.config.agent_messaging_enabled).toBe(false)
  expect((app.config.harness_mcp_enabled as Record<string,boolean>).claude).toBe(false)
  expect(app.writes.filter(write=>write.path==='/api/automation/provider/key')).toHaveLength(0)
})

test('voice local setup cannot complete until playback is confirmed',async({page})=>{
  await page.addInitScript(()=>{HTMLMediaElement.prototype.play=()=>Promise.resolve()})
  const app=await daemon(page,{step:'voice',draft:{voice:{step:'choices',read_aloud:true,dictation:false,tts_engine:'sapi',stt_engine:'whisper'}}})
  await page.goto('/onboarding-harness.html')
  await expect(page.getByRole('checkbox',{name:'Summarize long replies before reading'})).toBeDisabled()
  await next(page)
  await page.getByRole('button',{name:'Test voice',exact:true}).click()
  await expect(page.getByRole('button',{name:'Finish voice setup'})).toBeDisabled()
  expect(app.state().completed).not.toContain('voice')
  await page.getByRole('button',{name:'Speak a test sentence'}).click()
  await page.getByRole('checkbox',{name:'I heard the sentence'}).check()
  await page.getByRole('button',{name:'Finish voice setup'}).click()
  await expect(page.getByRole('heading',{name:'Anything else before you start?'})).toBeVisible()
  expect(app.state().completed).toContain('voice')
  expect(app.config.tts_content).toBe('verbatim')
})

test('dictation downloads are available and missing models block completion',async({page})=>{
  await daemon(page,{step:'voice',draft:{voice:{step:'choices',read_aloud:false,dictation:true,stt_engine:'whisper'}}})
  await page.goto('/onboarding-harness.html');await next(page)
  await expect(page.getByRole('button',{name:/Download turbo/})).toBeVisible()
  await expect(page.getByRole('button',{name:'Test voice',exact:true})).toBeDisabled()
})

test('adding models from Voice returns to Voice without enabling automations',async({page})=>{
  const app=await daemon(page,{step:'voice',draft:{model_features_pending:true,voice:{step:'choices',read_aloud:true,dictation:false,tts_engine:'sapi'}}})
  await page.goto('/onboarding-harness.html')
  await page.getByRole('button',{name:'Add provider',exact:true}).click()
  await page.getByLabel('Model provider',{exact:true}).selectOption('custom')
  await page.getByLabel('Base URL',{exact:true}).fill('http://127.0.0.1:11434/v1')
  await page.getByLabel('Single model (when there is no catalog)').fill('local-model')
  await page.getByRole('button',{name:'Save and test connection'}).click()
  await page.getByRole('button',{name:'Verify models and continue'}).click()
  await expect(page.getByRole('heading',{name:'What would you like voice to do?'})).toBeVisible()
  await expect(page.getByRole('checkbox',{name:'Summarize long replies before reading'})).toBeEnabled()
  expect(app.writes.some(write=>write.path==='/api/onboarding/features/activate')).toBe(false)
  expect(app.state().draft.model_features_pending).toBe(true)
})

test('retained settings, optional tour, and restoring hidden setup remain available',async({page})=>{
  const app=await daemon(page,{existing:true})
  await page.goto('/onboarding-harness.html')
  await page.getByRole('button',{name:'Keep settings',exact:true}).click()
  await page.getByRole('button',{name:'Take a short tour'}).click()
  await expect(page.locator('.tutorial-card')).toBeVisible()
  expect(app.state().status).toBe('complete')
  await page.getByRole('button',{name:'Exit tutorial'}).click()
  await page.getByRole('region',{name:'Getting started',exact:true}).getByRole('button',{name:/^[▾▸] Getting started/}).click()
  await page.getByRole('button',{name:'Hide Getting started',exact:true}).click()
  await expect(page.getByRole('button',{name:'Hide Getting started',exact:true})).toHaveCount(0)
  await page.getByRole('button',{name:'Show Getting started again'}).click()
  await expect(page.getByRole('region',{name:'Getting started',exact:true}).getByRole('button',{name:/^[▾▸] Getting started/})).toBeVisible()
})

test('core pages and optional guides stay within the phone viewport',async({page})=>{
  await page.setViewportSize({width:390,height:844})
  await daemon(page)
  await core(page)
  await page.getByRole('button',{name:'Add a Project later'}).click()
  for(const step of ['keymap','permissions','extras']){
    const box=await page.locator('.onboarding-flow').boundingBox()
    expect(box!.x).toBeGreaterThanOrEqual(0);expect(box!.x+box!.width).toBeLessThanOrEqual(390)
    await page.screenshot({path:`../.trash/onboarding-new-mobile-${step}.png`})
    if(step==='keymap')await page.getByRole('button',{name:'Continue optional setup'}).click()
    if(step==='permissions')await next(page)
  }
})

test('provider form drafts survive Back without storing the API key',async({page})=>{
  const app=await daemon(page)
  await optionalModels(page)
  await page.getByLabel('Model provider',{exact:true}).selectOption('custom')
  await page.getByLabel('Base URL',{exact:true}).fill('http://127.0.0.1:1234/v1')
  await page.getByLabel('Single model (when there is no catalog)').fill('my-local-model')
  await page.getByLabel('API key (optional for local servers)').fill('draft-secret')
  await page.getByRole('button',{name:'Back',exact:false}).first().click()
  await expect(page.getByRole('heading',{name:'How much should happen automatically?'})).toBeVisible()
  await next(page)
  await expect(page.getByLabel('Base URL',{exact:true})).toHaveValue('http://127.0.0.1:1234/v1')
  await expect(page.getByLabel('Single model (when there is no catalog)')).toHaveValue('my-local-model')
  expect(JSON.stringify(app.state())).not.toContain('draft-secret')
})

test('returning through an unchanged experience keeps later feature customization',async({page})=>{
  const app=await daemon(page)
  await core(page)
  await page.getByRole('button',{name:'Back',exact:false}).first().click()
  await page.getByRole('button',{name:'Back',exact:false}).first().click()
  await next(page)
  expect(app.writes.filter(write=>write.path==='/api/experience-tier')).toHaveLength(1)
})
