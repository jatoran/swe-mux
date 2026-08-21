import { render } from 'preact'
import { useState } from 'preact/hooks'
import { GitTab, type GitView } from '../../src/GitTab'
import type { Project, Session } from '../../src/types'
import '../../src/style.css'

// Bulk select and pending removals, against an inventory the spec controls.
//
// The point this page exists to make testable: a removal's *response* is not what
// ends the removing indication. The DELETE here answers immediately and the worktree
// keeps being listed until the spec says otherwise, which is exactly the slow path -
// Git unlinking a dependency tree for the next twenty seconds while the request has
// already returned.

const project={id:'swe-mux',name:'swe-mux',root:'D:\\PROJECTS\\swe-mux'} as Project
const response=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}})
const clean={total:0,additions:0,deletions:0,binary_files:0,files:[],truncated:false}
const dirty={total:3,additions:9,deletions:1,binary_files:0,files:[],truncated:false}

const paths={
  clean:'D:\\PROJECTS\\swe-mux\\.claude\\worktrees\\wt-clean',
  dirty:'D:\\PROJECTS\\swe-mux\\.claude\\worktrees\\wt-dirty',
  busy:'D:\\PROJECTS\\swe-mux\\.claude\\worktrees\\wt-busy',
}

/** Paths the daemon has been asked to remove but whose directories the spec has not
 *  released yet. */
const removing=new Set<string>()
/** Paths the spec has released: gone from the inventory from the next read on. */
const gone=new Set<string>()
/** Paths whose removal the daemon should refuse, so a spec can drive the failure path. */
const refuse=new Set<string>()
const landed:string[]=[]
Object.assign(globalThis,{
  __landed:landed,
  __removing:removing,
  __refuse:(path:string)=>refuse.add(path),
  __finishRemoval:()=>{for(const path of removing)gone.add(path);removing.clear()},
})

const worktree=(path:string,branch:string,extra:Record<string,unknown>)=>({
  worktree:path,HEAD:`${branch.padEnd(40,'0')}`.slice(0,40),branch:`refs/heads/${branch}`,
  detached:false,bare:false,locked:null,prunable:null,main:false,
  head_committed_at:1786800000,comparison_counts:{ahead:0,behind:0},
  conflicted:clean,unstaged:clean,staged:clean,branch_delta:clean,...extra,
})

globalThis.fetch=async(input,init)=>{
  const url=String(input)
  if(url.startsWith('/api/git/worktrees')&&init?.method==='DELETE'){
    const path=String(JSON.parse(String(init.body)).path)
    if(refuse.has(path))return response({error:'git refused to remove it',code:'git_error'},400)
    removing.add(path)
    return response({ok:true,operation_id:'op',repaired:false,cleanup:{status:'purging',path:'graveyard'}})
  }
  if(url.startsWith('/api/git/worktrees'))return response({
    repository:{root:project.root,common_dir:'D:\\PROJECTS\\swe-mux\\.git'},
    comparison:{ref:'origin/master',display:'origin/master',source:'origin_head',available:true,reason:null,candidates:['origin/master']},
    worktrees:[
      {worktree:project.root,HEAD:'9299950aa1bb2cc3dd4ee5ff6001122334455667',branch:'refs/heads/master',
        detached:false,bare:false,main:true,head_committed_at:1786700000,comparison_counts:{ahead:0,behind:0},
        conflicted:clean,unstaged:clean,staged:clean,branch_delta:clean},
      worktree(paths.clean,'wt-clean',{}),
      worktree(paths.dirty,'wt-dirty',{unstaged:dirty,comparison_counts:{ahead:2,behind:0}}),
      worktree(paths.busy,'wt-busy',{}),
    ].filter(tree=>!gone.has(tree.worktree)),
  })
  if(url.startsWith('/api/git/provenance?'))return response({items:[]})
  if(url.startsWith('/api/land')&&init?.method==='POST'){
    landed.push(String(JSON.parse(String(init.body)).worktree_root))
    return response({id:`land-${landed.length}`},201)
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
  id:'busy-session',name:'claude-busy',project_id:project.id,state:'running',cwd:paths.busy,
  runtime_cwd_live:true,runtime_cwd:paths.busy,git:{branch:'wt-busy',dirty:0,ahead:0,behind:0},
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
