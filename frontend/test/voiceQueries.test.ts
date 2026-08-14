import assert from 'node:assert/strict'
import type { Project, Session } from '../src/types.ts'
import { buildFleetReadModel } from '../src/fleetStatus.ts'
import {
  parseVoiceQuery, projectListPage, safeDuringSystemPlayback, sessionListPage,
  voiceHelpPage, voiceHelpText, voiceSessionFilterMatches,
} from '../src/voiceQueries.ts'

assert.deepEqual(parseVoiceQuery('read me the last response of focused pane verbatim'), {
  kind: 'read_reply', reference: 'focused', mode: 'verbatim',
})
assert.deepEqual(parseVoiceQuery('summarize the last reply from session two'), {
  kind: 'read_reply', reference: '2', mode: 'summary',
})
assert.deepEqual(parseVoiceQuery('read session 3 last response'), {
  kind: 'read_reply', reference: '3', mode: 'current',
})
assert.deepEqual(parseVoiceQuery('read the last reply verbatim'), {
  kind: 'read_reply', reference: 'current', mode: 'verbatim',
})
assert.deepEqual(parseVoiceQuery('list pending sessions and their statuses in current project'), {
  kind: 'list_sessions', filter: 'needs_me', scope: { kind: 'current' },
})
assert.deepEqual(parseVoiceQuery('list active sessions overall'), {
  kind: 'list_sessions', filter: 'active', scope: { kind: 'all' },
})
assert.deepEqual(parseVoiceQuery('list stuck sessions in project one'), {
  kind: 'list_sessions', filter: 'stuck', scope: { kind: 'project', reference: '1' },
})
assert.deepEqual(parseVoiceQuery('status of session two'), {
  kind: 'status', entity: 'session', reference: '2', scope: { kind: 'all' },
})
assert.deepEqual(parseVoiceQuery('open project 3'), { kind: 'open', entity: 'project', reference: '3' })
assert.deepEqual(parseVoiceQuery('go to project1'), { kind: 'open', entity: 'project', reference: '1' })
assert.deepEqual(parseVoiceQuery('GoToProject, Project1'), { kind: 'open', entity: 'project', reference: '1' })
assert.deepEqual(parseVoiceQuery('go to project, project one'), { kind: 'open', entity: 'project', reference: '1' })
assert.deepEqual(parseVoiceQuery('go to project 1 session 2'), {
  kind:'open',entity:'session',projectReference:'1',reference:'2',
})
assert.deepEqual(parseVoiceQuery('GoToProject Project1 Session2'), {
  kind:'open',entity:'session',projectReference:'1',reference:'2',
})
assert.deepEqual(parseVoiceQuery('open session two in project one'), {
  kind:'open',entity:'session',projectReference:'1',reference:'2',
})
assert.deepEqual(parseVoiceQuery('what are the possible voice commands for navigation'), { kind: 'help', category: 'navigation' })
assert.deepEqual(parseVoiceQuery('read me possible voice commands'), { kind: 'help', category: null })
assert.deepEqual(parseVoiceQuery('help with session statuses'), { kind: 'help', category: 'sessions' })
assert.deepEqual(parseVoiceQuery('what can I say'), { kind: 'help', category: null })
assert.deepEqual(parseVoiceQuery('voice commands for approvals'), { kind: 'help', category: 'approvals' })
assert.deepEqual(parseVoiceQuery('list approvals'), {
  kind: 'list_sessions', filter: 'approval', scope: { kind: 'all' },
})
assert.deepEqual(parseVoiceQuery('show me questions in this project'), {
  kind: 'list_sessions', filter: 'question', scope: { kind: 'current' },
})
assert.deepEqual(parseVoiceQuery('active sessions'), {
  kind: 'list_sessions', filter: 'active', scope: { kind: 'all' },
})
assert.deepEqual(parseVoiceQuery('do I have pending sessions in the current project'), {
  kind: 'list_sessions', filter: 'needs_me', scope: { kind: 'current' },
})
assert.deepEqual(parseVoiceQuery('list current project sessions'), {
  kind: 'list_sessions', filter: 'live', scope: { kind: 'current' },
})
assert.deepEqual(parseVoiceQuery('current project active sessions'), {
  kind: 'list_sessions', filter: 'active', scope: { kind: 'current' },
})
assert.deepEqual(parseVoiceQuery('pending sessions current project'), {
  kind: 'list_sessions', filter: 'needs_me', scope: { kind: 'current' },
})
assert.deepEqual(parseVoiceQuery('list active sessions project alpha'), {
  kind: 'list_sessions', filter: 'active', scope: { kind: 'project', reference: 'alpha' },
})
assert.deepEqual(parseVoiceQuery('list project alpha pending sessions'), {
  kind: 'list_sessions', filter: 'needs_me', scope: { kind: 'project', reference: 'alpha' },
})
assert.deepEqual(parseVoiceQuery('focus on session two'), { kind: 'open', entity: 'session', reference: '2' })
assert.deepEqual(parseVoiceQuery('take me to project Alpha'), { kind: 'open', entity: 'project', reference: 'alpha' })
assert.equal(safeDuringSystemPlayback('open session two'), true)
assert.equal(safeDuringSystemPlayback('open project one session two'), true)
assert.equal(safeDuringSystemPlayback('confirm approval'), false)
assert.equal(safeDuringSystemPlayback('kill session two'), false)
assert.doesNotMatch(voiceHelpText(null), /\bmux\b/i)

const project = { id: 'p', name: 'Alpha' } as Project
const session = (id:string, state:Session['state'], awaiting:Session['awaiting_reason']=null):Session => ({
  id, name:`Agent ${id}`, project_id:'p', backend:'claude', native_session_id:'', cwd:'', exe:'', args:[], pid:1,
  created_at:900, state, tokens_in:0, process_job_assignment:'', tokens_out:0, tokens_cache_read:0, tokens_cache_write:0, cost_usd:0,
  context_window:0, context_pct:0, last_activity_ts:990, git:{dirty:0,ahead:0,behind:0}, pinned_attention:false, broadcast:false,
  context_peak_pct:0, compaction_count:0, runtime_cwd:'', runtime_cwd_live:true, runtime_cwd_source:'pty', runtime_cwd_dropped:0,
  awaiting_reason:awaiting, measurement_source:'pty_screen', delivery_readiness:{state:awaiting?'blocked':'safe',reason:awaiting||'ready',authorized:false},
})
const model = buildFleetReadModel([
  session('one','working'), session('two','awaiting','approval'), session('three','idle'),
  session('four','crashed'), session('five','awaiting','question'), session('six','running'),
], [project], 1000)
assert.deepEqual(model.sessions.filter(item=>voiceSessionFilterMatches(item,'needs_me')).map(item=>item.session.id), ['two','five'])
assert.deepEqual(model.sessions.filter(item=>voiceSessionFilterMatches(item,'active')).map(item=>item.session.id), ['one','six'])
const page = sessionListPage(model.sessions, 0, 5)
assert.match(page.speech, /Next session\. Session 2\. Name, Agent two\. Project, Alpha\. Status, awaiting your approval\./)
assert.match(page.speech, /1 more session\. Say, next page, to continue\./)
assert.match(page.detail, /Session 2 - Agent two\nProject: Alpha\nStatus: awaiting your approval/)
assert.equal(page.shownThrough, 5)
assert.match(sessionListPage(model.sessions, 5).speech, /Session 6/)
const addresses=new Map(model.sessions.map((item,index)=>[item.session.id,{projectNumber:3,sessionNumber:index+1}]))
const canonicalPage=sessionListPage([model.sessions[1],model.sessions[4]],0,5,false,{
  addressFor:item=>addresses.get(item.session.id)||null,
  compound:false,
})
assert.match(canonicalPage.speech,/Session 2\. Name, Agent two\./)
assert.match(canonicalPage.speech,/Session 5\. Name, Agent five\./)
const compoundPage=sessionListPage([model.sessions[4]],0,5,false,{
  addressFor:item=>addresses.get(item.session.id)||null,
  compound:true,
})
assert.match(compoundPage.detail,/Project 3, Session 5 - Agent five/)
assert.equal(projectListPage([{name:'Alpha'},{name:'Beta'}]).speech, 'Project list. 2 projects. Project 1. Name, Alpha. Next project. Project 2. Name, Beta. End of project list.')
assert.match(projectListPage([{id:'beta',name:'Beta'}],0,5,project=>project.id==='beta'?8:null).speech,/Project 8\. Name, Beta\./)
const helpPage=voiceHelpPage('sessions')
assert.match(helpPage.speech,/Command 1\. list sessions\. Next command\. Command 2\./)
assert.match(helpPage.speech,/End of sessions commands\./)
assert.match(helpPage.detail,/1\. list sessions\n2\. list active sessions/)

const dynamicHelp=voiceHelpPage('sessions',[{
  id:'session.spawn:p1:codex',label:'New Codex in Alpha',category:'session',available:true,run:()=>{},
  voice:{phrases:['new Codex','new Codex in Alpha']},
}])
assert.match(dynamicHelp.detail,/Start sessions in Projects/)
assert.match(dynamicHelp.detail,/new Codex in Alpha/)
assert.match(dynamicHelp.speech,/New Codex in Alpha/)

const completeHelp=voiceHelpPage(null,[{
  id:'drawer.close',label:'Close side panel',category:'view',available:true,run:()=>{},
  voice:{phrases:['close side panel','close right sidebar']},
}],[{action:'send',phrases:['send it']}])
assert.match(completeHelp.detail,/Configured conversation controls/)
assert.match(completeHelp.detail,/send it/)
assert.match(completeHelp.detail,/Workspace and side panels/)
assert.match(completeHelp.detail,/close right sidebar/)
assert.match(completeHelp.speech,/complete voice command catalog is in Talk history/i)

console.log('voice query tests passed')
