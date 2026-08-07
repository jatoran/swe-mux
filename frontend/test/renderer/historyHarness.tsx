import { render } from 'preact'
import { HistoryBrowser, type HistoryEntry } from '../../src/HistoryBrowser'
import type { Project } from '../../src/types'
import '../../src/style.css'

const entry:HistoryEntry={
  id:'history-layout',native_id:'native-layout',backend:'claude',name:'Optimize Workout Plan',cwd:'D:\\PROJECTS\\workout-plan',
  spawned_at:1786113405,last_message_at:1786118091,last_message_role:'assistant',project_id:'workout-plan',project_label:'workout-plan',
  final_state:'killed',model:'claude-fable-5',external:0,tokens_in:521346,tokens_out:2130,cost_usd:0,
  provider:'anthropic',provider_account_hashes:{anthropic:'a'.repeat(64),bedrock:'b'.repeat(64),vertex:'c'.repeat(64)},
  transcript_size:3_145_728,compaction_capability:'native',compaction_confidence:'unavailable',
}
const project={id:'workout-plan',name:'workout-plan',root:'D:\\PROJECTS\\workout-plan'} as Project
const response=(body:unknown)=>new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json'}})

globalThis.fetch=async(input)=>{
  const url=String(input)
  if(url==='/api/history/projects')return response({items:[{project_id:project.id,label:project.name,root:project.root,sessions:1,last_activity:entry.last_message_at}]})
  if(url.startsWith('/api/history/backfills?'))return response({items:[]})
  if(url.startsWith(`/api/history/${entry.id}/transcript`))return response({entry,messages:[{role:'user',ts:'2026-08-07T13:36:45Z',content:[{type:'text',text:'Optimize my workout plan.'}]}],annotations:[],matches:[]})
  if(url.startsWith('/api/lineage?'))return response({items:[]})
  if(url.startsWith('/api/history?'))return response({items:[entry],next_cursor:null})
  throw new Error(`Unexpected harness request: ${url}`)
}

render(<HistoryBrowser projects={[project]} initialProjectId={project.id} onClose={()=>undefined} onResume={()=>undefined} onSecondOpinion={()=>undefined} onHandoff={()=>undefined}/>,document.querySelector('#root')!)
