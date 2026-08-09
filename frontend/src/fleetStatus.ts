import type { Project, Session } from './types.ts'

export type FleetConfidence = 'proven' | 'inferred' | 'unknown'
export type FleetClaim<T> = {
  value: T
  source: string
  observedAt: number
  ageSeconds: number
  confidence: FleetConfidence
}

export type FleetSession = {
  session: Session
  projectName: string
  state: FleetClaim<Session['state']>
  awaiting: FleetClaim<string | null>
  delivery: FleetClaim<'safe' | 'blocked' | 'unknown'>
  activity: FleetClaim<number>
}

export type FleetReadModel = {
  observedAt: number
  sessions: FleetSession[]
  counts: Record<Session['state'], number>
}

export type FleetPredicate = 'approval' | 'question' | 'rate_limit' | 'working' | 'idle' | 'stuck' | 'crashed'

const STATES: Session['state'][] = ['starting','running','working','idle','awaiting','exited','crashed']

const claim = <T>(value:T, source:string, observedAt:number, ageSeconds:number, confidence:FleetConfidence):FleetClaim<T> => ({
  value, source, observedAt, ageSeconds: Math.max(0, Math.round(ageSeconds)), confidence,
})

/** A projection of the current session ledger, recomputed from snapshots and never cached. */
export function buildFleetReadModel(sessions: Session[], projects: Project[], now = Date.now() / 1000): FleetReadModel {
  const names = new Map(projects.map(project => [project.id, project.name]))
  const counts = Object.fromEntries(STATES.map(state => [state, 0])) as Record<Session['state'],number>
  const items = sessions.filter(session => !session.pending).map(session => {
    counts[session.state] += 1
    const activityAt = session.last_activity_ts || session.created_at || now
    const activityAge = now - activityAt
    const source = session.measurement_source || 'session ledger'
    const delivery = session.delivery_readiness?.state || 'unknown'
    return {
      session,
      projectName: names.get(session.project_id) || session.project_label || 'Unknown project',
      state: claim(session.state, source, now, activityAge, session.measurement_source ? 'proven' : 'inferred'),
      awaiting: claim(session.awaiting_reason || null, session.awaiting_reason ? source : 'session ledger', now, activityAge, session.state === 'awaiting' ? 'proven' : 'inferred'),
      delivery: claim(delivery, session.delivery_readiness?.reason || 'readiness unavailable', now, activityAge, session.delivery_readiness ? 'proven' : 'unknown'),
      activity: claim(activityAt, source, now, activityAge, activityAt ? 'proven' : 'unknown'),
    }
  })
  return { observedAt: now, sessions: items, counts }
}

export function fleetPredicateMatches(item: FleetSession, predicate: FleetPredicate): boolean {
  if (predicate === 'approval') return item.awaiting.value === 'approval'
  if (predicate === 'question') return item.awaiting.value === 'question' || item.awaiting.value === 'elicitation'
  if (predicate === 'rate_limit') return item.awaiting.value === 'rate_limit'
  if (predicate === 'working') return item.state.value === 'working' || item.state.value === 'running'
  if (predicate === 'idle') return item.state.value === 'idle'
  if (predicate === 'crashed') return item.state.value === 'crashed'
  return !['exited','crashed'].includes(item.state.value)
    && (item.delivery.value === 'unknown' || (item.state.value === 'working' && item.activity.ageSeconds > 300))
}

export function sessionsMatchingFleetPredicate(model:FleetReadModel, predicate:FleetPredicate):FleetSession[] {
  return model.sessions.filter(item => fleetPredicateMatches(item,predicate))
}

const plural = (count:number, noun:string):string => `${count} ${noun}${count === 1 ? '' : 's'}`

/** Short by default because an open phone microphone cannot reliably reject its own TTS. */
export function fleetRundown(model:FleetReadModel):string {
  const active = model.counts.running + model.counts.working
  const approvals = sessionsMatchingFleetPredicate(model,'approval').length
  const questions = sessionsMatchingFleetPredicate(model,'question').length
  const problems = model.counts.crashed + sessionsMatchingFleetPredicate(model,'stuck').length
  return `${plural(model.sessions.length,'session')}: ${active} active, ${approvals} awaiting approval, ${questions} awaiting an answer, ${problems} needing attention.`
}

export function fleetRundownDetail(model:FleetReadModel):string {
  if (!model.sessions.length) return 'No sessions are running.'
  return model.sessions.map(item => {
    const reason = item.awaiting.value ? ` awaiting ${item.awaiting.value.replace('_',' ')}` : ` ${item.state.value}`
    const freshness = item.activity.ageSeconds < 2 ? 'now' : `${item.activity.ageSeconds} seconds ago`
    return `${item.session.name} in ${item.projectName} is${reason}; observed ${freshness} from ${item.state.source}.`
  }).join(' ')
}
