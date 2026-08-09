import assert from 'node:assert/strict'
import type { Project, Session } from '../src/types.ts'
import { buildFleetReadModel } from '../src/fleetStatus.ts'
import {
  parseVoiceQuery, projectListPage, safeDuringSystemPlayback, sessionListPage,
  voiceHelpText, voiceSessionFilterMatches,
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
assert.deepEqual(parseVoiceQuery('what are the possible voice commands for navigation'), { kind: 'help', category: 'navigation' })
assert.equal(safeDuringSystemPlayback('open session two'), true)
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
assert.match(page.speech, /Session 2, Agent two, in Alpha, awaiting your approval/)
assert.match(page.speech, /1 more\. Say next to continue/)
assert.equal(page.shownThrough, 5)
assert.match(sessionListPage(model.sessions, 5).speech, /Session 6/)
assert.equal(projectListPage([{name:'Alpha'},{name:'Beta'}]).speech, '2 projects. Project 1, Alpha. Project 2, Beta.')

console.log('voice query tests passed')
