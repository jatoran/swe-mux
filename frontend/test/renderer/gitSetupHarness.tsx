import { render } from 'preact'
import { GitTab } from '../../src/GitTab'
import type { Project } from '../../src/types'
import '../../src/style.css'

const project={id:'project',name:'Project',root:'D:\\PROJECTS\\project'} as Project
const params=new URL(location.href).searchParams
const tracked=params.has('tracked')
const notRepository=params.has('notrepo')
const decisions:unknown[]=[]
let promptEnabled=true
Object.assign(globalThis,{
  __setupDecisions:decisions,
  __enableSetupPrompt:()=>{promptEnabled=true;window.dispatchEvent(new Event('mux:install-config-changed'))},
})

const response=(body:unknown,status=200)=>new Response(JSON.stringify(body),{
  status,headers:{'Content-Type':'application/json'},
})
const clean={total:0,additions:0,deletions:0,binary_files:0,files:[],truncated:false}

globalThis.fetch=async(input,init)=>{
  const url=String(input)
  if(url.startsWith('/api/git/worktrees?')){
    if(notRepository)return response({error:'Project folder is not a Git repository',code:'not_git_repository'},404)
    return response({
      repository:{root:project.root,common_dir:`${project.root}\\.git`},
      comparison:{ref:'main',display:'main',source:'local_fallback',available:true,reason:null,candidates:['main']},
      worktrees:[{worktree:project.root,HEAD:'a'.repeat(40),branch:'refs/heads/main',detached:false,
        bare:false,locked:null,prunable:null,main:true,comparison_counts:{ahead:0,behind:0},
        conflicted:clean,unstaged:clean,staged:clean,branch_delta:clean}],
    })
  }
  if(url.startsWith('/api/git/swe-mux-setup?'))return response({
    show:promptEnabled,reason:promptEnabled?(tracked?'tracked':'available'):'disabled',
    decision:'unseen',can_ignore:promptEnabled&&!tracked,tracked,
  })
  if(url==='/api/git/swe-mux-setup'){
    const body=JSON.parse(String(init?.body))
    decisions.push(body)
    if(body.decision==='never_ask')promptEnabled=false
    return response({ok:true,decision:body.decision,changed:body.decision==='ignore_all'})
  }
  if(url==='/api/git/init'){
    decisions.push(JSON.parse(String(init?.body)))
    return response({branch:'main',gitignore:'created',swe_mux_ignore:'added'})
  }
  if(url.startsWith('/api/land/verify-command'))return response({
    configured:false,source:'',display:'',digest:'',approved:false,previously_approved:false,
    approved_source:'',current_source:'',config_command:'',config_revision:'r1',
    config_status:'ready',config_path:`${project.root}\\.swe-mux\\config.toml`,
    script_name:'.worktree-verify',script_present:false,plan:null,
  })
  if(url.startsWith('/api/land'))return response({
    requests:[],hourly_budget:12,hold_timeout_seconds:1800,retry_verification:false,
    installed_enabled:false,project_enabled:false,agent_grant:'draft',
  })
  throw new Error(`Unexpected harness request: ${url}`)
}

render(<aside class="utility-drawer" style="width:520px;height:100dvh"><GitTab
  view="map" onView={()=>undefined} project={project} sessions={[]}
  onOpenFile={()=>undefined} onOpenWorktreeFile={()=>undefined} onProjectUpdated={()=>undefined}
  onOpenSession={()=>undefined} onOpenHistory={()=>undefined}
/></aside>,document.querySelector('#root')!)
