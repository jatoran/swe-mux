import assert from 'node:assert/strict'
import test from 'node:test'
import { combinedResourceTotals, projectResourceTotals } from '../src/resourceTotals.ts'

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
    processes:6,cpu_pct:14,memory_bytes:400,
  })
})
