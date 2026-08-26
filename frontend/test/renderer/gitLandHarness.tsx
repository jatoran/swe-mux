import { render } from 'preact'
import { useState } from 'preact/hooks'
import { GitTab, type GitView } from '../../src/GitTab'
import { DrawerSegmentControl } from '../../src/DrawerSegmentControl'
import type { Project, Session } from '../../src/types'
import '../../src/style.css'

// The Map with a gate actually running, which is the state the whole progress reading
// exists for and the one a static screenshot of a real daemon cannot be relied on to
// catch. `gitMapHarness.tsx` is the quiet case - an approved gate and an empty queue, so
// the row offers its Land button; this page is the busy one, and is also where the
// landing strip's own summary line and verification editor are exercised.

const project={id:'swe-mux',name:'swe-mux',root:'D:\\PROJECTS\\swe-mux'} as Project
const response=(body:unknown)=>new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json'}})
const worktree='C:\\Users\\Jatora\\.mux\\worktrees\\swe-mux-29a044bb\\land-ui-rework'
const betaWorktree='D:\\wt\\beta'
const cleanSummary={total:0,additions:0,deletions:0,binary_files:0,files:[],truncated:false}

/** Every write the page attempts, so the spec can prove the editor sends one key. */
const writes:{url:string;body:unknown}[]=[]
Object.assign(globalThis,{__writes:writes})

// `?blocked=1` serves an unapproved gate from the *first* read, which is the state the
// strip has to open itself for. `?unconfigured=1` serves a repository that has no
// verification command at all, which is the state it must NOT open itself for. Both have
// to arrive on the first read: reaching either by editing the command instead would mean
// the reader had already toggled the strip open by hand, so the default could never be
// observed - and the default is the whole claim.
const parameters=new URLSearchParams(location.search)
const blocked=parameters.has('blocked')
const unconfigured=parameters.has('unconfigured')

globalThis.fetch=async(input,init)=>{
  const url=String(input)
  const method=String(init?.method||'GET').toUpperCase()
  if(url.startsWith('/api/git/worktrees?'))return response({
    repository:{root:project.root,common_dir:'D:\\PROJECTS\\swe-mux\\.git'},
    comparison:{ref:'origin/main',display:'origin/main',source:'origin_head',available:true,reason:null,candidates:['origin/main']},
    worktrees:[
      {worktree,HEAD:'a'.repeat(40),branch:'refs/heads/worktree-land-ui-rework',detached:false,bare:false,main:false,
       comparison_counts:{ahead:3,behind:0},conflicted:cleanSummary,unstaged:cleanSummary,staged:cleanSummary,branch_delta:cleanSummary},
      {worktree:betaWorktree,HEAD:'e'.repeat(40),branch:'refs/heads/worktree-beta',detached:false,bare:false,main:false,
       comparison_counts:{ahead:2,behind:0},conflicted:cleanSummary,unstaged:cleanSummary,staged:cleanSummary,branch_delta:cleanSummary},
      {worktree:project.root,HEAD:'b'.repeat(40),branch:'refs/heads/master',detached:false,bare:false,main:true,
       comparison_counts:{ahead:0,behind:0},conflicted:cleanSummary,unstaged:cleanSummary,staged:cleanSummary,branch_delta:cleanSummary},
    ],
  })
  if(url.startsWith('/api/git/provenance?'))return response({items:[]})
  if(url.startsWith('/api/land/verify-command')){
    if(method==='PUT'){
      writes.push({url,body:JSON.parse(String(init?.body||'{}'))})
      return response({
        configured:true,source:'project_config',display:'./verify.sh',digest:'d2',
        approved:false,previously_approved:true,approved_source:'#!/usr/bin/env bash\nexit 0\n',
        current_source:'./verify.sh',config_command:'./verify.sh',config_revision:'r2',
        config_status:'ready',config_path:'D:\\PROJECTS\\swe-mux\\.swe-mux\\config.toml',
        script_name:'.worktree-verify',script_present:true,plan:null,
      })
    }
    // A repository that never set the queue up: no script at the convention's path, no
    // config key, and therefore nothing to approve. `config_status:'ready'` is deliberate
    // — the config file is perfectly writable, there is simply no command in it.
    if(unconfigured)return response({
      configured:false,source:'',display:'',digest:'',
      approved:false,previously_approved:false,approved_source:'',current_source:'',
      config_command:'',config_revision:'r1',config_status:'ready',
      config_path:'D:\\PROJECTS\\swe-mux\\.swe-mux\\config.toml',
      script_name:'.worktree-verify',script_present:false,plan:null,
    })
    return response({
      configured:true,source:'convention',display:'.worktree-verify',digest:'d1',
      approved:!blocked,previously_approved:true,approved_source:'#!/usr/bin/env bash\nexit 0\n',
      current_source:'#!/usr/bin/env bash\nexit 0\n',
      config_command:'',config_revision:'r1',config_status:'ready',
      config_path:'D:\\PROJECTS\\swe-mux\\.swe-mux\\config.toml',
      script_name:'.worktree-verify',script_present:true,
      // A measured statement about a previous run of these exact bytes. It is what
      // makes "of 7" a fact rather than an estimate, and it is drawn apart from
      // anything the running gate says so it cannot read as a prediction.
      plan:{steps:['pytest','ruff','mypy','mypy (per-platform implementations)','frontend tsc','frontend renderer tsc','frontend tests'],duration_ms:212_000,observed_at:1786800000},
    })
  }
  if(url.startsWith('/api/land'))return response({
    hourly_budget:12,hold_timeout_seconds:1800,retry_verification:false,
    installed_enabled:true,project_enabled:true,agent_grant:'draft',
    requests:[
      {id:'lnd_running',project_id:project.id,project_root:project.root,worktree_root:worktree,
       branch:'worktree-land-ui-rework',trunk_ref:'master',origin:'operator',state:'verifying',
       created_at:1786800000,updated_at:1786800100,
       verify_progress:{
         step_index:3,step_name:'mypy',expected_step_count:7,
         expected_steps:['pytest','ruff','mypy','mypy (per-platform implementations)','frontend tsc','frontend renderer tsc','frontend tests'],
         beyond_plan:false,
         completed_steps:[{name:'pytest',duration_ms:175_000},{name:'ruff',duration_ms:3_000}],
         lines:1204,elapsed_ms:190_000,step_elapsed_ms:12_000,attempt:1,attempts:1,finished:false,
       }},
      {id:'lnd_behind',project_id:project.id,project_root:project.root,worktree_root:betaWorktree,
       branch:'worktree-beta',trunk_ref:'master',origin:'agent',state:'queued',
       created_at:1786800050,updated_at:1786800050},
      {id:'lnd_done',project_id:project.id,project_root:project.root,worktree_root:'D:\\wt\\gamma',
       branch:'worktree-gamma',trunk_ref:'master',origin:'operator',state:'landed',
       created_at:1786700000,updated_at:1786700300,finished_at:1786700300,
       trunk_before:'c'.repeat(40),landed_oid:'d'.repeat(40)},
      // A verify-only request that cleared its gate. It moved no trunk, so it has no
      // OID pair to draw and must not read as a landing anywhere on this surface.
      {id:'lnd_checked',project_id:project.id,project_root:project.root,worktree_root:'D:\\wt\\delta',
       branch:'worktree-delta',trunk_ref:'master',origin:'agent',kind:'verify',state:'verified',
       created_at:1786700400,updated_at:1786700600,finished_at:1786700600},
    ],
  })
  throw new Error(`Unexpected harness request: ${url}`)
}

const session={id:'session',name:'claude-1',project_id:project.id,state:'running',cwd:project.root} as Session

function LandHarness() {
  // 'map' rather than a Land segment: there is no Land segment any more. The strip is at
  // the head of the map, and the retired id resolves here (`drawerSegments.ts`).
  const [view,setView]=useState<GitView>('map')
  return <aside class="utility-drawer" style="width:100%;height:100dvh;overflow:auto">
    <DrawerSegmentControl tab="git" active={view}
      context={{hasTranscript:true,isAgentSession:true}} onSelect={id=>setView(id as GitView)}/>
    <GitTab view={view} onView={setView} project={project} sessions={[session]}
      onOpenFile={()=>undefined} onOpenWorktreeFile={()=>undefined} onProjectUpdated={()=>undefined}
      onOpenSession={()=>undefined} onOpenHistory={()=>undefined}/>
  </aside>
}

render(<LandHarness/>, document.querySelector('#root')!)
