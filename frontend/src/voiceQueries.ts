import type { FleetSession } from './fleetStatus.ts'
import { fleetPredicateMatches } from './fleetStatus.ts'
import { normalizeSpokenText } from './voiceIntents.ts'
import { sessionDisplayName } from './sessionNames.ts'
import type { VoiceSessionAddress } from './voiceNavigation.ts'
import type { Command } from './commands.ts'
import {
  completeVoiceReference, VOICE_HELP_COMMANDS,
  type ConfiguredVoiceCommand, type VoiceHelpCategory,
} from './voiceCommandReference.ts'

export { VOICE_HELP_COMMANDS }
export type { VoiceHelpCategory }

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
export type VoiceQuery =
  | { kind: 'help'; category: VoiceHelpCategory | null }
  | { kind: 'list_projects' }
  | { kind: 'list_sessions'; filter: VoiceSessionFilter; scope: VoiceScope }
  | { kind: 'status'; entity: 'session' | 'project' | 'fleet'; reference: string; scope: VoiceScope }
  | { kind: 'open'; entity: 'project'; reference: string }
  | { kind: 'open'; entity: 'session'; reference: string; projectReference?: string }
  | { kind: 'read_reply'; reference: string; mode: VoiceReplyMode }
  | { kind: 'next' | 'repeat' | 'detail' }

export type SpokenPage = {
  speech: string
  detail: string
  shownFrom: number
  shownThrough: number
  hasMore: boolean
}

export type VoiceHelpPage = { speech:string; detail:string }

const helpCategory = (value: string): VoiceHelpCategory | null => {
  if (/approv|confirm/.test(value)) return 'approvals'
  if (/read|reply|response|summary|verbatim/.test(value)) return 'reading'
  if (/session|status|active|pending|approval|question|stuck|failed/.test(value)) return 'sessions'
  if (/project/.test(value)) return 'projects'
  if (/navigate|navigation|open|focus|switch/.test(value)) return 'navigation'
  if (/dictat|draft|send|undo|cancel|listen/.test(value)) return 'dictation'
  return null
}

const parseScope = (value: string): { text: string; scope: VoiceScope } => {
  let text = value.trim()
  if (/(?:\s+)(?:overall|globally|everywhere|across all projects|in all projects|for all projects)$/.test(text)) {
    return { text: text.replace(/(?:\s+)(?:overall|globally|everywhere|across all projects|in all projects|for all projects)$/, '').trim(), scope: { kind: 'all' } }
  }
  if (/(?:\s+)(?:(?:in|for|from|within) )?(?:the )?(?:current|this) project$/.test(text)) {
    return { text: text.replace(/(?:\s+)(?:(?:in|for|from|within) )?(?:the )?(?:current|this) project$/, '').trim(), scope: { kind: 'current' } }
  }
  const currentPrefix=text.match(/^(?:(?:list|show|read(?: out)?|tell me|give me)(?: me)?(?: the)? )?(?:in |for )?(?:the )?(?:current|this) project(?:s)?[,:]?\s+(.+)$/)
  if(currentPrefix)return{text:currentPrefix[1].trim(),scope:{kind:'current'}}
  const project = text.match(/(?:\s+)(?:(?:in|for|from|within) )?(?:the )?project (.+)$/)
  if (project) {
    text = text.slice(0, project.index).trim()
    return { text, scope: { kind: 'project', reference: project[1].trim() } }
  }
  return { text, scope: { kind: 'all' } }
}

const sessionFilter = (value: string): VoiceSessionFilter | null => {
  if (/\b(?:pending|attention|need me|needs me|needing me|waiting for me|require my attention)\b/.test(value)) return 'needs_me'
  if (/\bapprovals?\b|waiting for approval/.test(value)) return 'approval'
  if (/\bquestions?\b|waiting for (?:an )?answer|need(?:s)? an answer/.test(value)) return 'question'
  if (/rate[ -]?limit/.test(value)) return 'rate_limited'
  if (/\bstuck\b|unresponsive|not responding/.test(value)) return 'stuck'
  if (/\bfailed\b|\bfailures?\b|\bcrashed\b|\bcrashes\b/.test(value)) return 'failed'
  if (/\bactive\b|what is running|whats running/.test(value)) return 'active'
  if (/\bworking\b|\brunning\b/.test(value)) return 'working'
  if (/\bready\b|\bidle\b/.test(value)) return 'ready'
  if (/\b(?:all|live)?\s*(?:sessions?|agents?)\b|session statuses|agent statuses/.test(value)) return 'live'
  return null
}

const cleanReference = (value: string,entity?:'session'|'project'): string => value
  .replace(/^(?:the\s+)?/, '')
  .replace(/^focused pane$/, 'focused')
  .replace(/^(?:current|this) pane$/, 'current')
  .replace(/^focused session$/, 'focused')
  .replace(/^(?:current|this) session$/, 'current')
  .replace(entity?new RegExp(`^${entity}\\s+`):/^session\s+/, '')
  .replace(/\s+s$/, '')
  .trim()

const normalizeQueryText=(value:string):string=>normalizeSpokenText(value)
  .replace(/^goto(?=\s|project|session)/,'go to ')
  .replace(/\b(project|session)(\d+)\b/g,'$1 $2')
  .replace(/\s+/g,' ')

/** Closed, deterministic query grammar. It returns null for ordinary command aliases. */
export function parseVoiceQuery(value: string): VoiceQuery | null {
  const text = normalizeQueryText(value)
  if (!text) return null

  if (/^(?:next|next page|continue|keep going|more)$/.test(text)) return { kind: 'next' }
  if (/^(?:repeat|say that again|repeat that|repeat list)$/.test(text)) return { kind: 'repeat' }
  if (/^(?:details|more detail|give me details|full details)$/.test(text)) return { kind: 'detail' }

  const scopedHelp = text.match(/^(?:voice )?(?:commands?|help)(?: me)? (?:for|about|with) (.+)$/)
  if (scopedHelp) return { kind: 'help', category: helpCategory(scopedHelp[1] || '') }
  const help = text.match(/^(?:(?:voice )?help|help(?: me)?(?: with voice commands?)?|(?:voice )?commands?|(?:possible|available) voice commands?|(?:list|show|read(?: out)?|tell me|give me)(?: me)?(?: the)?(?: possible| available)?(?: voice)? commands?|what are(?: the)?(?: possible| available)?(?: voice)? commands?|what commands can i (?:say|use)|what can i say|what can you do)(?: (?:for|about) (.+))?$/)
  if (help) return { kind: 'help', category: helpCategory(help[1] || '') }

  if (/^(?:list|show|read|tell me|give me)(?: the)?(?: all)? projects?$|^(?:all )?projects$/.test(text)) return { kind: 'list_projects' }

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

  const compoundProjectFirst=text.match(/^(?:open|go to|focus(?: on)?|switch to|show me|take me to) (?:the )?project (.+?) (?:the )?session (.+)$/)
  if(compoundProjectFirst)return{
    kind:'open',entity:'session',reference:cleanReference(compoundProjectFirst[2],'session'),
    projectReference:cleanReference(compoundProjectFirst[1],'project'),
  }
  const compoundSessionFirst=text.match(/^(?:open|go to|focus(?: on)?|switch to|show me|take me to) (?:the )?session (.+?) (?:in|from|within) (?:the )?project (.+)$/)
  if(compoundSessionFirst)return{
    kind:'open',entity:'session',reference:cleanReference(compoundSessionFirst[1],'session'),
    projectReference:cleanReference(compoundSessionFirst[2],'project'),
  }
  let match = text.match(/^(?:open|go to|focus(?: on)?|switch to|show me|take me to) (?:the )?(session|project) (.+)$/)
  if (match) {
    const entity=match[1] as 'session'|'project'
    return { kind: 'open', entity, reference: cleanReference(match[2],entity) }
  }

  match = text.match(/^status (?:of )?(session|project) (.+)$/)
  if (match) return { kind: 'status', entity: match[1] as 'session' | 'project', reference: cleanReference(match[2]), scope: { kind: 'all' } }
  match = text.match(/^(session|project) (.+?) status$/)
  if (match) return { kind: 'status', entity: match[1] as 'session' | 'project', reference: cleanReference(match[2]), scope: { kind: 'all' } }
  if (/^status (?:of )?(?:the )?(?:current|this) project$/.test(text)) {
    return { kind: 'status', entity: 'fleet', reference: '', scope: { kind: 'current' } }
  }
  const projectFirst=text.match(/^(?:(?:list|show|read(?: out)?|tell me|give me)(?: me)?(?: the)? )?project (.+?) ((?:(?:all|live|active|pending|working|running|ready|idle|stuck|failed|crashed|rate limited) )?(?:sessions?|agents?)|approvals?|questions?)$/)
  if(projectFirst){
    const filter=sessionFilter(projectFirst[2])
    if(filter)return{kind:'list_sessions',filter,scope:{kind:'project',reference:projectFirst[1]}}
  }
  const scoped = parseScope(text)
  const hasListLead=/^(?:list|show|read(?: out)?|tell me|give me|which are|which|what is|whats|what are|what|are there(?: any)?|do i have(?: any)?)\b/.test(scoped.text)
  scoped.text = scoped.text
    .replace(/(?: and)?(?: their)? statuses?$/, '')
    .replace(/^(?:list|show|read(?: out)?|tell me|give me|which are|which|what is|whats|what are|what|are there(?: any)?|do i have(?: any)?)(?: me)?(?: the)?(?: all)?\s+/, '')
    .trim()
  if (/^(?:fleet status|status report|overall status|session status|sessions status|agent status|agents status|status)$/.test(scoped.text)) {
    return { kind: 'status', entity: 'fleet', reference: '', scope: scoped.scope }
  }
  const isBareList=/^(?:(?:all|live|active|pending|working|running|ready|idle|stuck|failed|crashed|rate limited) )?(?:sessions?|agents?)$|^sessions? (?:needing me|waiting for (?:my |an )?(?:approval|answer))$|^(?:approvals?|questions?)$|^(?:what is|whats) running$/.test(scoped.text)
  if(hasListLead||isBareList){
    const filter=sessionFilter(scoped.text)
    if(filter)return{kind:'list_sessions',filter,scope:scoped.scope}
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

export type SessionListAddressing={
  addressFor:(item:FleetSession)=>VoiceSessionAddress|null
  compound:boolean
}

export function sessionListPage(
  items:FleetSession[],offset=0,limit=5,detailed=false,addressing?:SessionListAddressing,
):SpokenPage{
  const start = Math.max(0, Math.min(offset, items.length))
  const page = items.slice(start, start + limit)
  if (!page.length) return { speech: 'There are no more sessions in that list.', detail: 'There are no more sessions in that list.', shownFrom: start, shownThrough: start, hasMore: false }
  const speechItems = page.map((item, index) => {
    const address=addressing?.addressFor(item)
    const number=address?.sessionNumber??start+index+1
    const project=addressing?.compound&&address?`Project ${address.projectNumber}, `:''
    const boundary=index?'Next session. ':''
    return `${boundary}${project}Session ${number}. Name, ${sessionDisplayName(item.session)}. Project, ${item.projectName}. Status, ${spokenSessionStatus(item, detailed)}.`
  }).join(' ')
  const detailItems=page.map((item,index)=>{
    const address=addressing?.addressFor(item)
    const number=address?.sessionNumber??start+index+1
    const project=addressing?.compound&&address?`Project ${address.projectNumber}, `:''
    return`${project}Session ${number} - ${sessionDisplayName(item.session)}\nProject: ${item.projectName}\nStatus: ${spokenSessionStatus(item,detailed)}`
  }).join('\n\n')
  const remaining=items.length-start-page.length
  const speechTail=remaining>0?`${remaining} more session${remaining===1?'':'s'}. Say, next page, to continue.`:'End of session list.'
  const detailTail=remaining>0?`\n\n${remaining} more. Say “next page” to continue.`:'\n\nEnd of list.'
  const speech=`Session list. ${items.length} matching session${items.length===1?'':'s'}. ${speechItems} ${speechTail}`
  const detail=`${items.length} matching session${items.length===1?'':'s'}\n\n${detailItems}${detailTail}`
  return { speech, detail, shownFrom: start, shownThrough: start + page.length, hasMore: remaining > 0 }
}

export function projectListPage(
  projects:Array<{id?:string;name:string}>,offset=0,limit=5,numberFor?:(project:{id?:string;name:string})=>number|null,
):SpokenPage{
  const start = Math.max(0, Math.min(offset, projects.length))
  const page = projects.slice(start, start + limit)
  if (!page.length) return { speech: 'There are no more projects in that list.', detail: 'There are no more projects in that list.', shownFrom: start, shownThrough: start, hasMore: false }
  const speechItems=page.map((project,index)=>`${index?'Next project. ':''}Project ${numberFor?.(project)??start+index+1}. Name, ${project.name}.`).join(' ')
  const detailItems=page.map((project,index)=>`Project ${numberFor?.(project)??start+index+1} - ${project.name}`).join('\n')
  const remaining=projects.length-start-page.length
  const speechTail=remaining>0?`${remaining} more project${remaining===1?'':'s'}. Say, next page, to continue.`:'End of project list.'
  const detailTail=remaining>0?`\n${remaining} more. Say “next page” to continue.`:'\nEnd of list.'
  const speech=`Project list. ${projects.length} project${projects.length===1?'':'s'}. ${speechItems} ${speechTail}`
  const detail=`${projects.length} project${projects.length===1?'':'s'}\n\n${detailItems}\n${detailTail}`
  return { speech, detail, shownFrom: start, shownThrough: start + page.length, hasMore: remaining > 0 }
}

const helpGroup=(category:VoiceHelpCategory):VoiceHelpPage=>{
  const title=category[0].toUpperCase()+category.slice(1)
  const commands=VOICE_HELP_COMMANDS[category]
  const speech=`${title} commands. ${commands.map((command,index)=>`${index?'Next command. ':''}Command ${index+1}. ${command}.`).join(' ')} End of ${category} commands.`
  const detail=`${title} commands\n${commands.map((command,index)=>`${index+1}. ${command}`).join('\n')}`
  return{speech,detail}
}

export function voiceHelpPage(
  category:VoiceHelpCategory|null,
  commands:Command[]=[],
  configuredCommands:ConfiguredVoiceCommand[]=[],
):VoiceHelpPage{
  const sections=completeVoiceReference(commands,configuredCommands,category)
  const detail=sections.map(section=>{
    const phrases=section.phrases.map((phrase,index)=>`${index+1}. ${phrase}`)
    const dynamic=section.commands.map(command=>{
      const availability=command.available?'':` [unavailable: ${command.disabledReason||'current workspace state'}]`
      return [`- ${command.label}${availability}`,...command.phrases.map(phrase=>`  - ${phrase}`)].join('\n')
    })
    return[section.title,...phrases,...dynamic].join('\n')
  }).join('\n\n')
  if(!category){
    const summary=sections.map(section=>`${section.title}, ${section.phrases.length+section.commands.length}`).join('. ')
    return{
      speech:`Complete voice command catalog is in Talk history. Groups and command counts: ${summary}. Ask for voice commands for a group to hear its entries.`,
      detail,
    }
  }
  if(!commands.length&&!configuredCommands.length)return helpGroup(category)
  let number=0
  const spoken=sections.flatMap(section=>[
    `${section.title}.`,
    ...section.phrases.map(phrase=>`Command ${++number}. ${phrase}.`),
    ...section.commands.map(command=>{
      const aliases=command.phrases.map(phrase=>`Say ${phrase}.`).join(' ')
      return`Command ${++number}. ${command.label}. ${aliases}`
    }),
  ]).join(' ')
  return{speech:`${spoken} End of ${category} commands.`,detail}
}

export function voiceHelpText(category:VoiceHelpCategory|null):string{return voiceHelpPage(category).speech}

/** Only read-only lookup and navigation queries may interrupt trusted application speech. */
export function safeDuringSystemPlayback(value: string): boolean {
  const query = parseVoiceQuery(value)
  return !!query && ['help', 'list_projects', 'list_sessions', 'status', 'open', 'read_reply', 'next', 'repeat', 'detail'].includes(query.kind)
}
