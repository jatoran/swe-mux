import { render } from 'preact'
import { GitTab } from '../../src/GitTab'
import type { Project, Session } from '../../src/types'
import '../../src/style.css'

const project={id:'swe-mux',name:'swe-mux',root:'D:\\PROJECTS\\swe-mux'} as Project
const response=(body:unknown)=>new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json'}})
const worktreeHead='c6824e7d0123456789abcdef0123456789abcdef'
const mainHead='9299950aa1bb2cc3dd4ee5ff6001122334455667'
const cleanSummary={total:0,additions:0,deletions:0,binary_files:0,files:[],truncated:false}

globalThis.fetch=async input=>{
  const url=String(input)
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
  if(url.startsWith('/api/git/provenance?'))return response({items:[]})
  throw new Error(`Unexpected harness request: ${url}`)
}

const session={
  id:'session',name:'session',project_id:project.id,state:'running',cwd:project.root,
  runtime_cwd_live:true,runtime_cwd:'C:\\Users\\Jatora\\.mux\\worktrees\\swe-mux-29a044bb\\sidebar-session-git-lines-fix',
  git:{branch:'sidebar-session-git-lines-fix',dirty:33,ahead:444,behind:555},
} as Session

render(<aside class="utility-drawer" style="width:100%;height:100dvh"><GitTab project={project} sessions={[session]} onOpenFile={()=>undefined} onOpenWorktreeFile={()=>undefined} onProjectUpdated={()=>undefined}/></aside>,document.querySelector('#root')!)
