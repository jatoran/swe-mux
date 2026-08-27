import { render } from 'preact'
import { useState } from 'preact/hooks'
import { GitTab, type GitView } from '../../src/GitTab'
import { DrawerSegmentControl } from '../../src/DrawerSegmentControl'
import type { Project, Session } from '../../src/types'
import '../../src/style.css'

const project={id:'swe-mux',name:'swe-mux',root:'D:\\PROJECTS\\swe-mux'} as Project
const response=(body:unknown)=>new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json'}})
const worktreeHead='c6824e7d0123456789abcdef0123456789abcdef'
const mainHead='9299950aa1bb2cc3dd4ee5ff6001122334455667'
const cleanSummary={total:0,additions:0,deletions:0,binary_files:0,files:[],truncated:false}

globalThis.fetch=async input=>{
  const url=String(input)
  if(url.startsWith('/api/git/swe-mux-setup?'))return response({show:false,reason:'decided',decision:'keep_visible',can_ignore:false,tracked:false})
  if(url.startsWith('/api/git/worktrees?'))return response({
    repository:{root:project.root,common_dir:'D:\\PROJECTS\\swe-mux\\.git'},
    comparison:{ref:'origin/main',display:'origin/main',source:'origin_head',available:true,reason:null,candidates:['origin/main']},
    worktrees:[{
      worktree:'C:\\Users\\Jatora\\.mux\\worktrees\\swe-mux-29a044bb\\sidebar-session-git-lines-fix',
      HEAD:worktreeHead,branch:'refs/heads/sidebar-session-git-lines-fix',
      detached:false,bare:false,locked:'locked for a renderer test',prunable:'stale renderer test',main:false,
      comparison_counts:{ahead:12345,behind:67890},
      conflicted:{total:0,additions:0,deletions:0,binary_files:0,files:[],truncated:false},
      unstaged:{total:22,additions:50,deletions:8,binary_files:0,files:[],truncated:true},
      staged:{total:11,additions:20,deletions:4,binary_files:0,files:[],truncated:true},
      branch_delta:{total:0,additions:0,deletions:0,binary_files:0,files:[],truncated:false},
    },{
      worktree:project.root,HEAD:mainHead,branch:'refs/heads/main',
      detached:false,bare:false,main:true,comparison_counts:{ahead:0,behind:0},
      conflicted:cleanSummary,unstaged:cleanSummary,staged:cleanSummary,branch_delta:cleanSummary,
    }],
  })
  if(url.startsWith('/api/git/graph?'))return response({
    lines:[
      {kind:'commit',graph:'* ',oid:worktreeHead,parents:[mainHead],refs:['sidebar-session-git-lines-fix'],author:'Codex',committed_at:1786800000,subject:'Fix sidebar Git lines'},
      {kind:'commit',graph:'* ',oid:mainHead,parents:[],refs:['HEAD','origin/main'],author:'Jatora',committed_at:1786700000,subject:'Main branch baseline'},
    ],
    limit:80,has_more:false,
  })
  if(url.startsWith('/api/git/provenance?'))return response({items:[
    {
      id:'p-live',session_id:'session',session_name:'claude-0e7d93',display_name:'Fix sidebar Git lines',
      history_id:'run-live',agent_run_id:'run-live',project_id:project.id,worktree_root:project.root,
      commit_oid:worktreeHead,parent_oids:[mainHead],subject:'Fix sidebar Git lines',committed_at:1786800000,
      relationship:'created',confidence:'exact',ambiguous:false,role:'committer',
      match_method:'command_range',contributed_paths:['frontend/src/App.tsx'],source:'session_tool',observed_at:1786800001,
    },
    {
      id:'p-ended',session_id:'gone',session_name:'claude-aa11bb',display_name:'Land the migration',
      history_id:'run-ended',agent_run_id:'run-ended',project_id:project.id,worktree_root:project.root,
      commit_oid:worktreeHead,parent_oids:[mainHead],subject:'Fix sidebar Git lines',committed_at:1786800000,
      relationship:'contributed',confidence:'correlated',ambiguous:false,role:'contributor',
      match_method:'tier0_write',contributed_paths:['frontend/src/style.css'],source:'tier0_write',observed_at:1786800002,
    },
  ]})
  // Landing is drawn on the Map row now, so the tab reads the queue and this checkout's
  // verification gate. Nothing is queued here: this page is about the row offering the
  // act, and `git-land-harness.html` is about the queue reporting a running one.
  if(url.startsWith('/api/land/verify-command'))return response({
    configured:true,source:'convention',display:'.worktree-verify',digest:'d1',
    approved:true,previously_approved:true,approved_source:'#!/usr/bin/env bash\nexit 0\n',
    current_source:'#!/usr/bin/env bash\nexit 0\n',
    config_command:'',config_revision:'r1',config_status:'ready',
    config_path:'D:\\PROJECTS\\swe-mux\\.swe-mux\\config.toml',
    script_name:'.worktree-verify',script_present:true,plan:null,
  })
  if(url.startsWith('/api/land'))return response({
    requests:[],hourly_budget:12,hold_timeout_seconds:1800,retry_verification:false,
    installed_enabled:true,project_enabled:true,agent_grant:'draft',
  })
  throw new Error(`Unexpected harness request: ${url}`)
}

/** What a click on a session link asked the app to do, for the specs to read back. */
const followed:string[]=[]
Object.assign(globalThis,{__followed:followed})

const session={
  id:'session',name:'claude-0e7d93',generated_title:'Fix sidebar Git lines',agent_run_id:'run-live',
  project_id:project.id,state:'running',cwd:project.root,
  runtime_cwd_live:true,runtime_cwd:'C:\\Users\\Jatora\\.mux\\worktrees\\swe-mux-29a044bb\\sidebar-session-git-lines-fix',
  git:{branch:'sidebar-session-git-lines-fix',dirty:33,ahead:444,behind:555},
} as Session

// Map/Log/Provenance is a registered drawer segment now, so the harness mounts the same
// shared control the real drawer draws above the tab, driven by the same registry. Without
// it the spec would have no way to reach Log or Provenance, and the geometry it measures
// would not be the geometry the app renders.
function GitHarness() {
  const [view,setView]=useState<GitView>('map')
  return <aside class="utility-drawer" style="width:100%;height:100dvh">
    <DrawerSegmentControl
      tab="git"
      active={view}
      context={{hasTranscript:true,isAgentSession:true}}
      onSelect={id=>setView(id as GitView)}
    />
    <GitTab
      view={view} onView={setView}
      project={project} sessions={[session]}
      onOpenFile={()=>undefined} onOpenWorktreeFile={()=>undefined} onProjectUpdated={()=>undefined}
      onOpenSession={id=>followed.push(`session:${id}`)}
      onOpenHistory={id=>followed.push(`history:${id}`)}
    />
  </aside>
}

render(<GitHarness/>, document.querySelector('#root')!)
