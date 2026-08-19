import type { Command } from './commands.ts'

export type VoiceHelpCategory = 'reading' | 'sessions' | 'projects' | 'navigation' | 'dictation' | 'approvals'

export const VOICE_HELP_COMMANDS:Record<VoiceHelpCategory,string[]>={
  reading:['read the last reply','read the last reply verbatim','summarize the last reply','read Session 2 reply','read a session reply by name'],
  sessions:['list sessions','list active sessions','list working sessions','list ready sessions','list pending sessions','list approvals','list questions','list rate limited sessions','list stuck sessions','list failed sessions','status of Session 2','list sessions in the current Project','list sessions in Project 2'],
  projects:['list Projects','open Project 2','open a Project by name','open Project 2 Session 1','status of Project 2','status of the current Project','list sessions in Project 2'],
  navigation:['go to next session','go to previous session','open Session 2 in the current Project','open Project 2','open Project 2 Session 1','open Session 1 in Project 2','open a session or Project by its visible name','next page','repeat','more detail'],
  dictation:['send','append without sending','voice comms on','voice comms off','undo last phrase','cancel','mute','summary mode','verbatim mode','interrupt agent','standby','resume','listen (hold a chat brainstorm)','go ahead (release the brainstorm)','stop listening','pin the current voice target from the Talk panel'],
  approvals:['open a session awaiting approval','approve','review approval','confirm tool use','confirm approval','cancel approval'],
}

export const VOICE_ACTION_ORDER=['send','append','cancel','undo','mute','read','summary','verbatim','interrupt','help','standby','resume','hold','proceed','comms_on','comms_off','stop'] as const

export const VOICE_ACTION_META:Record<string,{label:string;hint:string}>={
  send:{label:'Send / submit',hint:'submit the buffered message'},
  append:{label:'Append',hint:'append the buffer without submitting'},
  cancel:{label:'Cancel / clear',hint:'clear the whole draft'},
  undo:{label:'Undo',hint:'remove the last transcribed phrase'},
  mute:{label:'Mute',hint:'stop playback, keep listening'},
  read:{label:'Read reply',hint:'speak the latest reply'},
  summary:{label:'Summary mode',hint:'switch spoken replies to summaries'},
  verbatim:{label:'Verbatim mode',hint:'switch spoken replies to verbatim'},
  interrupt:{label:'Interrupt',hint:'stop playback and send Ctrl-C to the agent'},
  help:{label:'Help',hint:'list the commands'},
  standby:{label:'Standby',hint:'keep listening but ignore speech until resumed'},
  resume:{label:'Resume',hint:'leave standby and act on speech again'},
  hold:{label:'Hold (brainstorm)',hint:'chat mode buffers your thinking instead of answering each pause'},
  proceed:{label:'Go ahead',hint:'send the held brainstorm to the assistant as one message'},
  comms_on:{label:'Voice Comms on',hint:'request short spoken replies from the focused agent'},
  comms_off:{label:'Voice Comms off',hint:'restore normal replies and prior read-aloud settings'},
  stop:{label:'Stop listening',hint:'turn conversation mode off and release the mic'},
}

export type ConfiguredVoiceCommand={action:string;phrases:string[]}

export type VoiceReferenceCommand={
  id:string
  label:string
  phrases:string[]
  available:boolean
  disabledReason?:string
}

export type VoiceReferenceGroup={
  id:'status'|'workspace'|'projects'|'sessions'|'launch'|'terminal'|'other'
  title:string
  commands:VoiceReferenceCommand[]
}

export type VoiceCatalogSection={
  id:string
  title:string
  phrases:string[]
  commands:VoiceReferenceCommand[]
}

const GROUPS:Array<{id:VoiceReferenceGroup['id'];title:string;matches:(id:string)=>boolean}>=[
  {id:'status',title:'Status and guarded approvals',matches:id=>id.startsWith('voice.')},
  {id:'workspace',title:'Workspace and side panels',matches:id=>id.startsWith('drawer.')||id.startsWith('sidebar.')},
  {id:'projects',title:'Current Projects by name',matches:id=>id.startsWith('project.focus:')},
  {id:'sessions',title:'Current sessions by name',matches:id=>id.startsWith('session.focus:')||id==='session.nextInProject'||id==='session.previousInProject'},
  {id:'launch',title:'Start sessions in Projects',matches:id=>id.startsWith('session.spawn:')},
  {id:'terminal',title:'Focused terminal commands',matches:id=>id.startsWith('terminal.')},
  {id:'other',title:'Other current commands',matches:()=>true},
]

const uniquePhrases=(phrases:string[])=>[...new Set(phrases.map(phrase=>phrase.trim()).filter(Boolean))]

/** Live registry aliases, grouped for visual and spoken discovery. */
export function registeredVoiceReference(commands:Command[]):VoiceReferenceGroup[]{
  const grouped=new Map<VoiceReferenceGroup['id'],VoiceReferenceCommand[]>()
  for(const command of commands){
    if(!command.voice||command.id==='voice.query')continue
    const phrases=uniquePhrases(command.voice.phrases)
    if(!phrases.length)continue
    const group=GROUPS.find(candidate=>candidate.matches(command.id))||GROUPS[GROUPS.length-1]
    const items=grouped.get(group.id)||[]
    items.push({
      id:command.id,label:command.label,phrases,available:command.available,
      ...(command.disabledReason?{disabledReason:command.disabledReason}:{}),
    })
    grouped.set(group.id,items)
  }
  return GROUPS.flatMap(group=>{
    const items=grouped.get(group.id)
    return items?.length?[{id:group.id,title:group.title,commands:items}]:[]
  })
}

const configuredActionsFor=(commands:ConfiguredVoiceCommand[],category:VoiceHelpCategory|null):VoiceReferenceCommand[]=>{
  const readingActions=new Set(['read','summary','verbatim'])
  return VOICE_ACTION_ORDER.flatMap(action=>{
    if(category==='reading'&&!readingActions.has(action))return[]
    if(category&&category!=='reading'&&category!=='dictation')return[]
    const phrases=uniquePhrases(commands.find(command=>command.action===action)?.phrases||[])
    if(!phrases.length)return[]
    const meta=VOICE_ACTION_META[action]
    return[{id:`capture:${action}`,label:meta.label,phrases,available:true}]
  })
}

const commandMatchesHelpCategory=(id:string,category:VoiceHelpCategory):boolean=>{
  if(category==='approvals')return id.startsWith('voice.approval')
  if(category==='reading')return false
  if(category==='sessions')return id.startsWith('session.')||id.startsWith('voice.fleet')
  if(category==='projects')return id.startsWith('project.focus:')||id.startsWith('session.spawn:')
  if(category==='navigation')return id.startsWith('sidebar.')||id.startsWith('drawer.')||id.startsWith('project.focus:')||id.startsWith('session.focus:')||id==='session.nextInProject'||id==='session.previousInProject'
  return id.startsWith('terminal.')
}

/**
 * Complete discoverable command surface.
 *
 * Fixed query grammar is represented as phrase groups. Configurable capture
 * actions and live registry commands retain their labels, availability, and all
 * declared aliases. The internal `{text}` catch-all is intentionally excluded.
 */
export function completeVoiceReference(
  commands:Command[],
  configuredCommands:ConfiguredVoiceCommand[]=[],
  category:VoiceHelpCategory|null=null,
):VoiceCatalogSection[]{
  const grammarCategories=category?[category]:Object.keys(VOICE_HELP_COMMANDS) as VoiceHelpCategory[]
  const sections:VoiceCatalogSection[]=grammarCategories.map(name=>({
    id:`grammar:${name}`,
    title:`${name[0].toUpperCase()+name.slice(1)} grammar`,
    phrases:VOICE_HELP_COMMANDS[name],
    commands:[],
  }))
  const configured=configuredActionsFor(configuredCommands,category)
  if(configured.length)sections.push({
    id:'capture',title:'Configured conversation controls',phrases:[],commands:configured,
  })
  for(const group of registeredVoiceReference(commands)){
    const current=category?group.commands.filter(command=>commandMatchesHelpCategory(command.id,category)):group.commands
    if(current.length)sections.push({id:group.id,title:group.title,phrases:[],commands:current})
  }
  return sections
}
