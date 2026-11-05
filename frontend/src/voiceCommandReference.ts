import { t } from './localization/index.ts'
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
  send:{get label() { return t("voiceCommandReference.520734e7b1") },get hint() { return t("voiceCommandReference.fa18a31a0a") }},
  append:{get label() { return t("voiceCommandReference.fc15cc0a80") },get hint() { return t("voiceCommandReference.6fd4f20e19") }},
  cancel:{get label() { return t("voiceCommandReference.0ea0f26a49") },get hint() { return t("voiceCommandReference.cb9097101e") }},
  undo:{get label() { return t("voiceCommandReference.a8283ade31") },get hint() { return t("voiceCommandReference.4e9325909b") }},
  mute:{get label() { return t("voiceCommandReference.8dd6857baf") },get hint() { return t("voiceCommandReference.427a7f7b80") }},
  read:{get label() { return t("voiceCommandReference.fd2dbead21") },get hint() { return t("voiceCommandReference.02b1212572") }},
  summary:{get label() { return t("voiceCommandReference.52d760bc2e") },get hint() { return t("voiceCommandReference.3deb12c850") }},
  verbatim:{get label() { return t("voiceCommandReference.2da2936426") },get hint() { return t("voiceCommandReference.4a0be40569") }},
  interrupt:{get label() { return t("voiceCommandReference.b625231412") },get hint() { return t("voiceCommandReference.9e2fe3d001") }},
  help:{get label() { return t("voiceCommandReference.b79cac926e") },get hint() { return t("voiceCommandReference.72cec24686") }},
  standby:{get label() { return t("voiceCommandReference.fcd589c1bc") },get hint() { return t("voiceCommandReference.042d3cfc9a") }},
  resume:{get label() { return t("voiceCommandReference.d640c7421d") },get hint() { return t("voiceCommandReference.7cd7711ff4") }},
  hold:{get label() { return t("voiceCommandReference.cbd221d5a1") },get hint() { return t("voiceCommandReference.3941e26538") }},
  proceed:{get label() { return t("voiceCommandReference.4a6d4a7f29") },get hint() { return t("voiceCommandReference.abbb093f07") }},
  comms_on:{get label() { return t("voiceCommandReference.297bda6676") },get hint() { return t("voiceCommandReference.94618c5b14") }},
  comms_off:{get label() { return t("voiceCommandReference.c185122157") },get hint() { return t("voiceCommandReference.80cbc4e8cb") }},
  stop:{get label() { return t("voiceCommandReference.94fa9b2363") },get hint() { return t("voiceCommandReference.b482e2a908") }},
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
  {id:'status',get title() { return t("voiceCommandReference.733783bd68") },matches:id=>id.startsWith('voice.')},
  {id:'workspace',get title() { return t("voiceCommandReference.05fd9d2be0") },matches:id=>id.startsWith('drawer.')||id.startsWith('sidebar.')},
  {id:'projects',get title() { return t("voiceCommandReference.c84044f4ff") },matches:id=>id.startsWith('project.focus:')},
  {id:'sessions',get title() { return t("voiceCommandReference.62c6c05fe9") },matches:id=>id.startsWith('session.focus:')||id==='session.nextInProject'||id==='session.previousInProject'},
  {id:'launch',get title() { return t("voiceCommandReference.18ab268616") },matches:id=>id.startsWith('session.spawn:')},
  {id:'terminal',get title() { return t("voiceCommandReference.ae050f6188") },matches:id=>id.startsWith('terminal.')},
  {id:'other',get title() { return t("voiceCommandReference.28aa6920a0") },matches:()=>true},
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
    get title() { return t("voiceCommandReference.d35ace64e3", { value1: name[0].toUpperCase()+name.slice(1) }) },
    phrases:VOICE_HELP_COMMANDS[name],
    commands:[],
  }))
  const configured=configuredActionsFor(configuredCommands,category)
  if(configured.length)sections.push({
    id:'capture',get title() { return t("voiceCommandReference.591fa4326f") },phrases:[],commands:configured,
  })
  for(const group of registeredVoiceReference(commands)){
    const current=category?group.commands.filter(command=>commandMatchesHelpCategory(command.id,category)):group.commands
    if(current.length)sections.push({id:group.id,title:group.title,phrases:[],commands:current})
  }
  return sections
}
