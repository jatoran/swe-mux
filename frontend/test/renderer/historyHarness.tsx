import { render } from 'preact'
import { HistoryBrowser, type HistoryEntry } from '../../src/HistoryBrowser'
import type { Project } from '../../src/types'
import '../../src/deviceMode'
import '../../src/style.css'

const entry:HistoryEntry={
  id:'history-layout',native_id:'native-layout',backend:'claude',name:'Optimize Workout Plan',cwd:'D:\\PROJECTS\\workout-plan',
  spawned_at:1786113405,last_message_at:1786118091,last_message_role:'assistant',project_id:'workout-plan',project_label:'workout-plan',
  final_state:'killed',model:'claude-fable-5',external:0,tokens_in:521346,tokens_out:2130,cost_usd:0,
  provider:'anthropic',provider_account_hashes:{anthropic:'a'.repeat(64),bedrock:'b'.repeat(64),vertex:'c'.repeat(64)},
  transcript_size:3_145_728,compaction_capability:'native',compaction_confidence:'unavailable',
}
/* A conversation far longer than one page, for paging, the window bound, and jumping to a
   match. Its reads wait for `releaseHistoryReads()`, so a spec can look at the reader in
   the moment between the click and the daemon's answer. */
const long:HistoryEntry={
  id:'history-long',native_id:'native-long',backend:'codex',name:'Long Conversation',cwd:'D:\\PROJECTS\\workout-plan',
  spawned_at:1786100000,last_message_at:1786110000,last_message_role:'assistant',project_id:'workout-plan',project_label:'workout-plan',
  final_state:'idle',model:'gpt-5',external:0,
}
const LONG_MESSAGES=1000
const LONG_MATCH=900
const project={id:'workout-plan',name:'workout-plan',root:'D:\\PROJECTS\\workout-plan'} as Project
const response=(body:unknown)=>new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json'}})
const indexState=(count:number)=>({available:true,indexed:true,current:true,indexing:false,message_count:count,abandoned_messages:0,tool_calls:0,indexed_at:1786118091})

let release:()=>void=()=>undefined
const gate=new Promise<void>(resolve=>{release=resolve})
Object.assign(window,{releaseHistoryReads:()=>release()})

/* Enough commits and timeline entries that a section which failed to collapse - or a
   timeline that failed to preview only its two newest - is visible as geometry. */
const provenance={items:Array.from({length:6},(value,index)=>({
  id:`prov-${index}`,session_id:'session-layout',session_name:'workout',project_id:project.id,
  worktree_root:project.root,commit_oid:`${index}`.repeat(40),subject:`commit subject number ${index}`,
  relationship:'created',confidence:'exact',role:'committer',source:'observer',observed_at:1786113000+index*60,
}))}
const scanRecords=Array.from({length:5},(value,index)=>({
  id:`scan-${index}`,agent_run_id:entry.id,t0:1786113000+index*600,t1:1786113300+index*600,
  work_phase:`phase-${index}`,summary:`behavioural summary number ${index}`,
}))

/** The daemon's paging rules (`HistoryIndex.history_messages_page`) over ordinals 0..count-1. */
const pageOf=(url:URL,count:number)=>{
  const number=(name:string)=>url.searchParams.has(name)?Number(url.searchParams.get(name)):null
  const limit=number('limit')??60
  const after=number('after'),before=number('before'),around=number('around')
  let from:number,to:number
  if(around!==null){from=Math.max(0,around-Math.floor(limit/2));to=Math.min(count,from+limit)}
  else if(before!==null){to=Math.min(count,before);from=Math.max(0,to-limit)}
  else{from=after===null?0:after+1;to=Math.min(count,from+limit)}
  const messages=Array.from({length:Math.max(0,to-from)},(value,index)=>{
    const ordinal=from+index
    return {ordinal,role:ordinal%2?'assistant':'user',ts:'2026-08-07T13:36:45Z',text:ordinal===LONG_MATCH?`the needle is here, message ${ordinal}`:`long conversation message ${ordinal}`}
  })
  return {messages,has_more_before:from>0,has_more_after:to<count}
}

globalThis.fetch=async(input)=>{
  const url=new URL(String(input),window.location.href)
  const path=url.pathname
  if(path==='/api/history/projects')return response({items:[{project_id:project.id,label:project.name,root:project.root,sessions:2,last_activity:entry.last_message_at}]})
  if(path==='/api/history/backfills')return response({items:[]})
  if(path==='/api/history')return response({items:[entry,long],next_cursor:null})
  if(path===`/api/history/${entry.id}/detail`)return response({entry,transcript:indexState(1)})
  if(path===`/api/history/${entry.id}/messages`)return response({messages:[{ordinal:0,role:'user',ts:'2026-08-07T13:36:45Z',text:'Optimize my workout plan.'}],has_more_before:false,has_more_after:false})
  if(path===`/api/history/${entry.id}/notes`)return response({annotations:[],scan_records:scanRecords})
  if(path===`/api/history/${entry.id}/matches`)return response({matches:[]})
  if(path===`/api/history/${entry.id}/tool-calls`)return response({messages:[],mtime_ns:1,size:1})
  if(path.startsWith(`/api/history/${long.id}/`))await gate
  if(path===`/api/history/${long.id}/detail`)return response({entry:long,transcript:indexState(LONG_MESSAGES)})
  if(path===`/api/history/${long.id}/messages`)return response(pageOf(url,LONG_MESSAGES))
  if(path===`/api/history/${long.id}/notes`)return response({annotations:[],scan_records:[]})
  if(path===`/api/history/${long.id}/matches`)return response({matches:url.searchParams.get('q')?.includes('needle')?[{ordinal:LONG_MATCH,role:'user',excerpt:'the needle is here'}]:[]})
  if(path===`/api/history/${long.id}/tool-calls`)return response({messages:[],mtime_ns:1,size:1})
  if(path==='/api/git/provenance')return response(provenance)
  if(path==='/api/lineage')return response({items:[]})
  throw new Error(`Unexpected harness request: ${url}`)
}

render(<HistoryBrowser projects={[project]} initialProjectId={project.id} onClose={()=>undefined} onResume={()=>undefined} onScheduleResume={()=>undefined} onHandoff={()=>undefined}/>,document.querySelector('#root')!)
