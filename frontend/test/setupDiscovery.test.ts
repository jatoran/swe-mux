import test from 'node:test'
import assert from 'node:assert/strict'
import { mergeSetupCandidates, setupPathKey } from '../src/setupDiscovery.ts'
import { setupHasStartedSession, wantsSetupModels } from '../src/setupActivation.ts'

test('project history merges Windows aliases and retains POSIX case distinctions',()=>{
  const item=(root:string,harness:string,time:number)=>({root,name:'Project',harnesses:[harness],last_activity:time,sessions:1,available:true})
  const rows=mergeSetupCandidates([[item('D:/Work/Repo','claude',2),item('/work/Repo','claude',1)],[item('d:\\work\\repo','codex',3),item('/work/repo','codex',4)]])
  assert.equal(rows.length,3)
  assert.equal(rows[0].root,'/work/repo')
  const windows=rows.find(row=>setupPathKey(row.root)==='d:/work/repo')!
  assert.equal(windows.sessions,2)
  assert.deepEqual(windows.harnesses,['claude','codex'])
})

test('provider setup follows the effective model features, including explicit opt-outs',()=>{
  assert.equal(wantsSetupModels({tier:'automations'}),true)
  assert.equal(wantsSetupModels({tier:'automations',overrides:{automation_enabled:false,scan_timeline_enabled:false}}),false)
  assert.equal(wantsSetupModels({tier:'deterministic'}),false)
  assert.equal(wantsSetupModels({tier:'terminal',overrides:{scan_timeline_enabled:true}}),true)
})

test('an optimistic launch cannot complete the first-session task',()=>{
  assert.equal(setupHasStartedSession([]),false)
  assert.equal(setupHasStartedSession([{id:'pending-not-started'}]),false)
  assert.equal(setupHasStartedSession([{id:'pending-not-started'},{id:'real-session'}]),true)
})
