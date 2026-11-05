import { t } from './localization/index.ts'
import { serverNow } from './serverClock.ts'
import { sessionDisplayName } from './sessionNames.ts'
import type { Project, Session } from './types.ts'
import type { VoiceSessionAddress } from './voiceNavigation.ts'

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
export function buildFleetReadModel(sessions: Session[], projects: Project[], now = serverNow()): FleetReadModel {
  const names = new Map(projects.map(project => [project.id, project.name]))
  const counts = Object.fromEntries(STATES.map(state => [state, 0])) as Record<Session['state'],number>
  const items = sessions.filter(session => !session.pending).map(session => {
    counts[session.state] += 1
    const activityAt = session.last_activity_ts || session.created_at || now
    const activityAge = now - activityAt
    const source = session.measurement_source || t("fleetStatus.82b29b5cf0")
    const delivery = session.delivery_readiness?.state || 'unknown'
    return {
      session,
      projectName: names.get(session.project_id) || session.project_label || t("fleetStatus.ed51cfeff0"),
      state: claim(session.state, source, now, activityAge, session.measurement_source ? 'proven' : 'inferred'),
      awaiting: claim(session.awaiting_reason || null, session.awaiting_reason ? source : t("fleetStatus.82b29b5cf0"), now, activityAge, session.state === 'awaiting' ? 'proven' : 'inferred'),
      delivery: claim(delivery, session.delivery_readiness?.reason || t("fleetStatus.cadde08963"), now, activityAge, session.delivery_readiness ? 'proven' : 'unknown'),
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

/** Short by default because an open phone microphone cannot reliably reject its own TTS. */
export function fleetRundown(model:FleetReadModel):string {
  const active = model.counts.running + model.counts.working
  const approvals = sessionsMatchingFleetPredicate(model,'approval').length
  const questions = sessionsMatchingFleetPredicate(model,'question').length
  const problems = model.counts.crashed + sessionsMatchingFleetPredicate(model,'stuck').length
  return t("fleetStatus.3a1ba67f92", { value1: model.sessions.length, value2: active, value3: approvals, value4: questions, value5: problems })
}

export function fleetRundownDetail(
  model:FleetReadModel,
  addressing?:{addressFor:(item:FleetSession)=>VoiceSessionAddress|null;compound:boolean},
):string{
  if (!model.sessions.length) return t("fleetStatus.e2e8c68312")
  return t("fleetStatus.17fa5fd09e", { value1: model.sessions.map((item,index) => {
    const reason = item.awaiting.value ? ` awaiting ${item.awaiting.value.replace('_',' ')}` : ` ${item.state.value}`
    const freshness = item.activity.ageSeconds < 2 ? 'now' : t("fleetStatus.f53e1ed3f4", { value1: item.activity.ageSeconds })
    const address=addressing?.addressFor(item)
    const project=addressing?.compound&&address?`Project ${address.projectNumber}, `:''
    return t("fleetStatus.22f775d9c2", { value1: (index) ? 'yes' : 'no', value2: project, value3: address?.sessionNumber??index+1, value4: sessionDisplayName(item.session), value5: item.projectName, value6: reason, value7: freshness, value8: item.state.source })
  }).join(' ') })
}
