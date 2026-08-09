import type { FleetSession } from './fleetStatus.ts'
import { fleetPredicateMatches } from './fleetStatus.ts'
import { normalizeSpokenText } from './voiceIntents.ts'

export type VoiceSessionFilter =
  | 'live'
  | 'active'
  | 'working'
  | 'ready'
  | 'needs_me'
  | 'approval'
  | 'question'
  | 'rate_limited'
  | 'stuck'
  | 'failed'

export type VoiceScope =
  | { kind: 'all' }
  | { kind: 'current' }
  | { kind: 'project'; reference: string }

export type VoiceReplyMode = 'current' | 'summary' | 'verbatim'
export type VoiceHelpCategory = 'reading' | 'sessions' | 'projects' | 'navigation' | 'dictation' | 'approvals'

export type VoiceQuery =
  | { kind: 'help'; category: VoiceHelpCategory | null }
  | { kind: 'list_projects' }
  | { kind: 'list_sessions'; filter: VoiceSessionFilter; scope: VoiceScope }
  | { kind: 'status'; entity: 'session' | 'project' | 'fleet'; reference: string; scope: VoiceScope }
  | { kind: 'open'; entity: 'session' | 'project'; reference: string }
  | { kind: 'read_reply'; reference: string; mode: VoiceReplyMode }
  | { kind: 'next' | 'repeat' | 'detail' }

export type SpokenPage = {
  speech: string
  detail: string
  shownFrom: number
  shownThrough: number
  hasMore: boolean
}

const helpCategory = (value: string): VoiceHelpCategory | null => {
  if (/read|reply|response|summary|verbatim/.test(value)) return 'reading'
  if (/session|status|active|pending|approval|question|stuck|failed/.test(value)) return 'sessions'
  if (/project/.test(value)) return 'projects'
  if (/navigate|navigation|open|focus|switch/.test(value)) return 'navigation'
  if (/dictat|draft|send|undo|cancel|listen/.test(value)) return 'dictation'
  if (/approv|confirm/.test(value)) return 'approvals'
  return null
}

const parseScope = (value: string): { text: string; scope: VoiceScope } => {
  let text = value.trim()
  if (/(?:\s+)(?:overall|everywhere|across all projects|in all projects)$/.test(text)) {
    return { text: text.replace(/(?:\s+)(?:overall|everywhere|across all projects|in all projects)$/, '').trim(), scope: { kind: 'all' } }
  }
  if (/(?:\s+)in (?:the )?(?:current|this) project$/.test(text)) {
    return { text: text.replace(/(?:\s+)in (?:the )?(?:current|this) project$/, '').trim(), scope: { kind: 'current' } }
  }
  const project = text.match(/(?:\s+)in project (.+)$/)
  if (project) {
    text = text.slice(0, project.index).trim()
    return { text, scope: { kind: 'project', reference: project[1].trim() } }
  }
  return { text, scope: { kind: 'all' } }
}

const sessionFilter = (value: string): VoiceSessionFilter | null => {
  if (/\b(?:pending|need me|needs me|needing me|waiting for me)\b/.test(value)) return 'needs_me'
  if (/\bapprovals?\b|waiting for approval/.test(value)) return 'approval'
  if (/\bquestions?\b|waiting for (?:an )?answer/.test(value)) return 'question'
  if (/rate limit/.test(value)) return 'rate_limited'
  if (/\bstuck\b|unresponsive/.test(value)) return 'stuck'
  if (/\bfailed\b|\bcrashed\b/.test(value)) return 'failed'
  if (/\bactive\b|what is running/.test(value)) return 'active'
  if (/\bworking\b|\brunning\b/.test(value)) return 'working'
  if (/\bready\b|\bidle\b/.test(value)) return 'ready'
  if (/\b(?:all|live)?\s*sessions?\b/.test(value)) return 'live'
  return null
}

const cleanReference = (value: string): string => value
  .replace(/^(?:the\s+)?/, '')
  .replace(/^focused pane$/, 'focused')
  .replace(/^(?:current|this) pane$/, 'current')
  .replace(/^focused session$/, 'focused')
  .replace(/^(?:current|this) session$/, 'current')
  .replace(/^session\s+/, '')
  .replace(/\s+s$/, '')
  .trim()

/** Closed, deterministic query grammar. It returns null for ordinary command aliases. */
export function parseVoiceQuery(value: string): VoiceQuery | null {
  const text = normalizeSpokenText(value)
  if (!text) return null

  if (/^(?:next|next page|continue|keep going|more)$/.test(text)) return { kind: 'next' }
  if (/^(?:repeat|say that again|repeat that|repeat list)$/.test(text)) return { kind: 'repeat' }
  if (/^(?:details|more detail|give me details|full details)$/.test(text)) return { kind: 'detail' }

  const help = text.match(/^(?:help|list|read|tell me|what are|what can i say)(?: me)?(?: the)?(?: possible| available)? voice commands?(?: for (.+))?$/)
  if (help) return { kind: 'help', category: helpCategory(help[1] || '') }

  if (/^(?:list|read|tell me)(?: the)? projects?$/.test(text)) return { kind: 'list_projects' }

  const summarize = text.match(/^summari[sz]e(?: the)?(?: last)? (?:reply|response)(?: (?:of|from|in) (.+))?$/)
  if (summarize) return { kind: 'read_reply', reference: cleanReference(summarize[1] || 'current'), mode: 'summary' }

  const read = text.match(/^(?:read|speak)(?: me)?(?: the)? (?:last )?(?:reply|response)(?: again)?(?: (?:of|from|in) (.+?))?(?: (?:as )?(summary|verbatim))?$/)
  if (read) return {
    kind: 'read_reply',
    reference: cleanReference(read[1] || 'current'),
    mode: (read[2] as VoiceReplyMode | undefined) || 'current',
  }
  const readLeadingTarget = text.match(/^(?:read|speak)(?: me)? (.+?) (?:last )?(?:reply|response)(?: again)?(?: (?:as )?(summary|verbatim))?$/)
  if (readLeadingTarget) return {
    kind: 'read_reply',
    reference: cleanReference(readLeadingTarget[1]),
    mode: (readLeadingTarget[2] as VoiceReplyMode | undefined) || 'current',
  }

  const scoped = parseScope(text)
  scoped.text = scoped.text.replace(/(?: and)?(?: their)? statuses?$/, '').trim()
  const list = scoped.text.match(/^(?:list|read|tell me|which|what)(?: me)?(?: the)? (.+?sessions?|sessions? .+)$/)
  if (list) {
    const filter = sessionFilter(list[1])
    if (filter) return { kind: 'list_sessions', filter, scope: scoped.scope }
  }
  if (/^what is running$/.test(scoped.text)) return { kind: 'list_sessions', filter: 'active', scope: scoped.scope }
  if (/^(?:fleet status|status report)$/.test(scoped.text)) return { kind: 'status', entity: 'fleet', reference: '', scope: scoped.scope }

  let match = text.match(/^(?:open|go to|focus|switch to) (session|project) (.+)$/)
  if (match) return { kind: 'open', entity: match[1] as 'session' | 'project', reference: cleanReference(match[2]) }

  match = text.match(/^status (?:of )?(session|project) (.+)$/)
  if (match) return { kind: 'status', entity: match[1] as 'session' | 'project', reference: cleanReference(match[2]), scope: { kind: 'all' } }
  match = text.match(/^(session|project) (.+?) status$/)
  if (match) return { kind: 'status', entity: match[1] as 'session' | 'project', reference: cleanReference(match[2]), scope: { kind: 'all' } }
  if (/^status (?:of )?(?:the )?(?:current|this) project$/.test(text)) {
    return { kind: 'status', entity: 'fleet', reference: '', scope: { kind: 'current' } }
  }
  return null
}

export function voiceSessionFilterMatches(item: FleetSession, filter: VoiceSessionFilter): boolean {
  if (filter === 'live') return !['exited', 'crashed'].includes(item.state.value)
  if (filter === 'active') return ['starting', 'running', 'working'].includes(item.state.value)
  if (filter === 'working') return fleetPredicateMatches(item, 'working')
  if (filter === 'ready') return fleetPredicateMatches(item, 'idle')
  if (filter === 'needs_me') return fleetPredicateMatches(item, 'approval') || fleetPredicateMatches(item, 'question')
  if (filter === 'approval') return fleetPredicateMatches(item, 'approval')
  if (filter === 'question') return fleetPredicateMatches(item, 'question')
  if (filter === 'rate_limited') return fleetPredicateMatches(item, 'rate_limit')
  if (filter === 'stuck') return fleetPredicateMatches(item, 'stuck')
  return fleetPredicateMatches(item, 'crashed')
}

export function spokenSessionStatus(item: FleetSession, detailed = false): string {
  let status: string = item.state.value
  if (item.awaiting.value === 'approval') status = 'awaiting your approval'
  else if (item.awaiting.value === 'question' || item.awaiting.value === 'elicitation') status = 'waiting for your answer'
  else if (item.awaiting.value === 'rate_limit') status = 'rate limited'
  else if (item.state.value === 'crashed') status = 'failed'
  else if (fleetPredicateMatches(item, 'stuck')) status = 'possibly stuck'
  else if (item.state.value === 'idle') status = 'ready'
  if (!detailed) return status
  const age = item.activity.ageSeconds < 2 ? 'just now' : `${item.activity.ageSeconds} seconds ago`
  return `${status}, observed ${age} from ${item.state.source}`
}

export function sessionListPage(items: FleetSession[], offset = 0, limit = 5, detailed = false): SpokenPage {
  const start = Math.max(0, Math.min(offset, items.length))
  const page = items.slice(start, start + limit)
  if (!page.length) return { speech: 'There are no more sessions in that list.', detail: 'There are no more sessions in that list.', shownFrom: start, shownThrough: start, hasMore: false }
  const body = page.map((item, index) => {
    const number = start + index + 1
    return `Session ${number}, ${item.session.name}, in ${item.projectName}, ${spokenSessionStatus(item, detailed)}.`
  }).join(' ')
  const tail = start + page.length < items.length ? ` ${items.length - start - page.length} more. Say next to continue.` : ''
  const speech = `${items.length} matching session${items.length === 1 ? '' : 's'}. ${body}${tail}`
  return { speech, detail: speech, shownFrom: start, shownThrough: start + page.length, hasMore: start + page.length < items.length }
}

export function projectListPage(projects: Array<{ name: string }>, offset = 0, limit = 5): SpokenPage {
  const start = Math.max(0, Math.min(offset, projects.length))
  const page = projects.slice(start, start + limit)
  if (!page.length) return { speech: 'There are no more projects in that list.', detail: 'There are no more projects in that list.', shownFrom: start, shownThrough: start, hasMore: false }
  const body = page.map((project, index) => `Project ${start + index + 1}, ${project.name}.`).join(' ')
  const tail = start + page.length < projects.length ? ` ${projects.length - start - page.length} more. Say next to continue.` : ''
  const speech = `${projects.length} project${projects.length === 1 ? '' : 's'}. ${body}${tail}`
  return { speech, detail: speech, shownFrom: start, shownThrough: start + page.length, hasMore: start + page.length < projects.length }
}

export function voiceHelpText(category: VoiceHelpCategory | null): string {
  const help: Record<VoiceHelpCategory, string> = {
    reading: 'Reading commands: read the last reply; read the last reply verbatim; summarize the last reply; read session 2 reply; mute.',
    sessions: 'Session commands: list active sessions; list pending sessions; list approvals; list stuck sessions; status of session 2; list active sessions in the current project.',
    projects: 'Project commands: list projects; open project 2; status of project 2; list sessions in project 2.',
    navigation: 'Navigation commands: open session 2; open project 2; open a session or project by its visible name; next; repeat; more detail.',
    dictation: 'Dictation commands: send; undo last phrase; cancel; standby; resume; stop listening; pin the current voice target from the Talk panel.',
    approvals: 'Approval commands: open a session awaiting approval; approve; listen to the exact operation; then confirm approval or cancel approval. Approval confirmation never works during playback.',
  }
  if (category) return help[category]
  return `Voice command groups are reading, sessions and status, projects, navigation, dictation, and approvals. ${help.reading} ${help.sessions} Say commands for a group to hear the rest.`
}

/** Only read-only lookup and navigation queries may interrupt trusted application speech. */
export function safeDuringSystemPlayback(value: string): boolean {
  const query = parseVoiceQuery(value)
  return !!query && ['help', 'list_projects', 'list_sessions', 'status', 'open', 'read_reply', 'next', 'repeat', 'detail'].includes(query.kind)
}
