import { render } from 'preact'
import { useState } from 'preact/hooks'
import { GitTab, type GitView } from '../../src/GitTab'
import type { Project, Session } from '../../src/types'
import '../../src/style.css'

// Shift-click range selection on Map, against an inventory long enough to have a middle.
//
// The bulk-select harness next door has three linked checkouts, which is one press
// either way from every interesting case. A range needs rows *between* the two clicks:
// ones that must be swept, one that is blocked and must be stepped over, and enough of
// them that the search box can hide some and the range still may not reach them.
//
// Explicit, descending `head_committed_at` so the map order is the alphabetical one and
// a spec can name a row's neighbours without re-deriving the sort.
//
// Short, letter-poor paths, deliberately: Map's filter matches branch *and* path, and a
// realistic `…\swe-mux\.claude\worktrees\wt-x` prefix is shared by every row, so almost
// any query a spec picks matches all of them and the filtering half of these specs
// silently tests nothing.

const project={id:'repo',name:'repo',root:'D:\\repo'} as Project
const response=(body:unknown)=>new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json'}})
const clean={total:0,additions:0,deletions:0,binary_files:0,files:[],truncated:false}

const root='D:\\wt'
/** In map order. `busy` is the one with a live session in it, so its checkbox refuses. */
const names=['alpha','bravo','busy','delta','echo','foxtrot'] as const
const pathOf=(name:string)=>`${root}\\${name}`

const landed:string[]=[]
Object.assign(globalThis,{__landed:landed})

const worktree=(name:string,index:number)=>({
  worktree:pathOf(name),HEAD:`${name}`.padEnd(40,'0').slice(0,40),branch:`refs/heads/wt-${name}`,
  detached:false,bare:false,locked:null,prunable:null,main:false,
  head_committed_at:1786800000-index,comparison_counts:{ahead:0,behind:0},
  conflicted:clean,unstaged:clean,staged:clean,branch_delta:clean,
})

globalThis.fetch=async(input,init)=>{
  const url=String(input)
  if(url.startsWith('/api/git/swe-mux-setup?'))return response({show:false,reason:'decided',decision:'keep_visible',can_ignore:false,tracked:false})
  if(url.startsWith('/api/git/worktrees'))return response({
    repository:{root:project.root,common_dir:'D:\\PROJECTS\\swe-mux\\.git'},
    comparison:{ref:'origin/master',display:'origin/master',source:'origin_head',available:true,reason:null,candidates:['origin/master']},
    worktrees:[
      {worktree:project.root,HEAD:'9299950aa1bb2cc3dd4ee5ff6001122334455667',branch:'refs/heads/trunk',
        detached:false,bare:false,main:true,head_committed_at:1786900000,comparison_counts:{ahead:0,behind:0},
        conflicted:clean,unstaged:clean,staged:clean,branch_delta:clean},
      ...names.map(worktree),
    ],
  })
  if(url.startsWith('/api/git/provenance?'))return response({items:[]})
  if(url.startsWith('/api/land')&&init?.method==='POST'){
    landed.push(String(JSON.parse(String(init.body)).worktree_root))
    return response({id:`land-${landed.length}`})
  }
  if(url.startsWith('/api/land/verify-command'))return response({
    configured:true,source:'convention',display:'.worktree-verify',digest:'d1',
    approved:true,previously_approved:true,approved_source:'exit 0\n',current_source:'exit 0\n',
    config_command:'',config_revision:'r1',config_status:'ready',
    config_path:'D:\\PROJECTS\\swe-mux\\.swe-mux\\config.toml',
    script_name:'.worktree-verify',script_present:true,plan:null,
  })
  if(url.startsWith('/api/land'))return response({
    requests:[],hourly_budget:12,hold_timeout_seconds:1800,retry_verification:false,
    installed_enabled:true,project_enabled:true,agent_grant:'draft',
  })
  if(url.startsWith('/api/projects/'))return response({enabled:[],available:[]})
  throw new Error(`Unexpected harness request: ${url}`)
}

const session={
  id:'busy-session',name:'claude-busy',project_id:project.id,state:'running',cwd:pathOf('busy'),
  runtime_cwd_live:true,runtime_cwd:pathOf('busy'),git:{branch:'wt-busy',dirty:0,ahead:0,behind:0},
} as Session

function Harness() {
  const [view,setView]=useState<GitView>('map')
  return <aside class="utility-drawer" style="width:100%;height:100dvh">
    <GitTab
      view={view} onView={setView}
      project={project} sessions={[session]}
      onOpenFile={()=>undefined} onOpenWorktreeFile={()=>undefined} onProjectUpdated={()=>undefined}
      onOpenSession={()=>undefined} onOpenHistory={()=>undefined}
    />
  </aside>
}

render(<Harness/>, document.querySelector('#root')!)
