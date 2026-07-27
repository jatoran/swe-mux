import assert from 'node:assert/strict'
import test from 'node:test'
import { combinedResourceTotals, projectResourceTotals } from '../src/resourceTotals.ts'
import { classifyTooling, duplicateToolingGroups } from '../src/resourceTooling.ts'

test('owned process resources aggregate by project and exclude exited processes',()=>{
  const process=(cpu_pct:number,memory_bytes:number,exited_at?:number)=>({cpu_pct,memory_bytes,exited_at,listeners:[],connections:[],conditions:[]})
  const snapshot={available:true,totals:{processes:3,cpu_pct:12.5,memory_bytes:300,listeners:0,connections:0},sessions:[
    {session_id:'a',project_id:'project-b',processes:[process(5,100),process(99,999,20)]},
    {session_id:'b',project_id:'project-a',processes:[process(7.5,200)]},
  ]}
  const projects=[{id:'project-a',name:'Alpha',position:0},{id:'project-b',name:'Beta',position:1}]
  const totals=projectResourceTotals(snapshot as never,[] as never,projects as never)
  assert.deepEqual(totals.map(item=>({label:item.label,processes:item.processes,cpu:item.cpu_pct,memory:item.memory_bytes})),[
    {label:'Alpha',processes:1,cpu:7.5,memory:200},
    {label:'Beta',processes:1,cpu:5,memory:100},
  ])
})

test('combined resource totals add daemon infrastructure without double-counting projects',()=>{
  const snapshot={
    sessions:[],
    totals:{processes:4,cpu_pct:12.5,memory_bytes:300},
    daemon:{pid:99,processes:2,cpu_pct:1.5,memory_bytes:100},
  }
  assert.deepEqual(combinedResourceTotals(snapshot),{
    processes:6,cpu_pct:14,memory_bytes:400,memory_unique_bytes:null,
  })
})

test('unique memory totals only when every contributor reported one',()=>{
  const both={
    sessions:[],
    totals:{processes:4,cpu_pct:1,memory_bytes:300,memory_unique_bytes:200},
    daemon:{pid:99,processes:2,cpu_pct:1,memory_bytes:100,memory_unique_bytes:60},
  }
  assert.equal(combinedResourceTotals(both as never).memory_unique_bytes,260)

  // A daemon that did not report unique memory must not silently shrink the total.
  const partial={
    sessions:[],
    totals:{processes:4,cpu_pct:1,memory_bytes:300,memory_unique_bytes:200},
    daemon:{pid:99,processes:2,cpu_pct:1,memory_bytes:100},
  }
  assert.equal(combinedResourceTotals(partial as never).memory_unique_bytes,null)
})

test('project totals carry unique memory and refuse to half-report it',()=>{
  const process=(memory_bytes:number,memory_unique_bytes?:number)=>
    ({cpu_pct:0,memory_bytes,memory_unique_bytes,listeners:[],connections:[],conditions:[]})
  const projects=[{id:'project-a',name:'Alpha',position:0},{id:'project-b',name:'Beta',position:1}]
  const snapshot={available:true,sessions:[
    {session_id:'a',project_id:'project-a',processes:[process(100,60),process(200,120)]},
    {session_id:'b',project_id:'project-b',processes:[process(100,60),process(200)]},
  ]}
  const totals=projectResourceTotals(snapshot as never,[] as never,projects as never)
  assert.deepEqual(totals.map(item=>[item.label,item.memory_bytes,item.memory_unique_bytes]),[
    ['Alpha',300,180],
    ['Beta',300,null],
  ])
})

test('language servers are classified by command line',()=>{
  assert.equal(classifyTooling('node C:\\npm\\node_modules\\pyright\\langserver.index.js --stdio'),'pyright')
  assert.equal(classifyTooling('node /npm/node_modules/typescript-language-server/lib/cli.mjs --stdio'),'typescript-language-server')
  assert.equal(classifyTooling('node d:\\repo\\node_modules\\typescript\\lib\\tsserver.js'),'tsserver')
  assert.equal(classifyTooling('node d:/repo/node_modules/typescript/lib/typingsInstaller.js'),'tsserver typings installer')
  assert.equal(classifyTooling('rust-analyzer'),'rust-analyzer')
  assert.equal(classifyTooling('claude.exe --session-id abc'),null)
  assert.equal(classifyTooling('C:\\WINDOWS\\system32\\conhost.exe 0x4'),null)
})

test('duplicated tooling is only reported across sessions, never within one',()=>{
  const proc=(command:string,memory_bytes:number,exited_at?:number)=>
    ({cpu_pct:0,memory_bytes,command,exited_at,listeners:[],connections:[],conditions:[]})
  const pyright='node /npm/node_modules/pyright/langserver.index.js --stdio'
  const tsserver='node /repo/node_modules/typescript/lib/tsserver.js'
  const snapshot={available:true,sessions:[
    {session_id:'a',project_id:'p',processes:[proc(pyright,300),proc(tsserver,200)]},
    {session_id:'b',project_id:'p',processes:[proc(pyright,100),proc('claude.exe',999)]},
    // A second tsserver inside one session is how tsserver normally runs, not duplication.
    {session_id:'c',project_id:'other',processes:[proc(tsserver,50),proc(tsserver,50)]},
  ]}

  const all=duplicateToolingGroups(snapshot as never)
  assert.deepEqual(all.map(group=>[group.tool,group.instances,group.sessions,group.memory_bytes,group.duplicate_bytes]),[
    ['pyright',2,2,400,100],
    ['tsserver',3,2,300,100],
  ])

  const scoped=duplicateToolingGroups(snapshot as never,'p')
  assert.deepEqual(scoped.map(group=>group.tool),['pyright'])
})

test('exited tooling processes never count as duplication',()=>{
  const pyright='node /npm/node_modules/pyright/langserver.index.js --stdio'
  const proc=(memory_bytes:number,exited_at?:number)=>
    ({cpu_pct:0,memory_bytes,command:pyright,exited_at,listeners:[],connections:[],conditions:[]})
  const snapshot={available:true,sessions:[
    {session_id:'a',project_id:'p',processes:[proc(300)]},
    {session_id:'b',project_id:'p',processes:[proc(100,42)]},
  ]}
  assert.deepEqual(duplicateToolingGroups(snapshot as never),[])
})
