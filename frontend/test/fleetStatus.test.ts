import assert from 'node:assert/strict'
import { buildFleetReadModel, fleetRundown, fleetRundownDetail, sessionsMatchingFleetPredicate } from '../src/fleetStatus.ts'
import type { Project, Session } from '../src/types.ts'

const project = {id:'p',name:'Alpha'} as Project
const session = (id:string, state:Session['state'], awaiting:Session['awaiting_reason']=null, last=990):Session => ({
  id,name:`Agent ${id}`,project_id:'p',backend:'claude',native_session_id:'',cwd:'',exe:'',args:[],pid:1,
  created_at:900,state,tokens_in:0,process_job_assignment:'',tokens_out:0,tokens_cache_read:0,tokens_cache_write:0,cost_usd:0,
  context_window:0,context_pct:0,last_activity_ts:last,git:{dirty:0,ahead:0,behind:0},pinned_attention:false,broadcast:false,
  context_peak_pct:0,compaction_count:0,runtime_cwd:'',runtime_cwd_live:true,runtime_cwd_source:'pty',runtime_cwd_dropped:0,
  awaiting_reason:awaiting,measurement_source:'pty_screen',delivery_readiness:{state:awaiting?'blocked':'safe',reason:awaiting||'ready',authorized:false},
})

const model = buildFleetReadModel([
  session('one','working'), session('two','awaiting','approval'), session('three','crashed',null,500),
],[project],1000)
assert.equal(model.sessions[0].state.source,'pty_screen')
assert.equal(model.sessions[0].activity.ageSeconds,10)
assert.equal(sessionsMatchingFleetPredicate(model,'approval')[0].session.id,'two')
assert.equal(sessionsMatchingFleetPredicate(model,'crashed')[0].session.id,'three')
assert.equal(fleetRundown(model),'3 sessions: 1 active, 1 awaiting approval, 0 awaiting an answer, 1 needing attention.')
assert.match(fleetRundownDetail(model),/Agent two in Alpha is awaiting approval/)

console.log('fleet status tests passed')
