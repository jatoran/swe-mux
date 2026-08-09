import type { Command } from './commands.ts'

export type VoiceReferenceCommand={
  id:string
  label:string
  phrases:string[]
  available:boolean
  disabledReason?:string
}

export type VoiceReferenceGroup={
  id:'status'|'workspace'|'projects'|'sessions'|'launch'|'other'
  title:string
  commands:VoiceReferenceCommand[]
}

const GROUPS:Array<{id:VoiceReferenceGroup['id'];title:string;matches:(id:string)=>boolean}>=[
  {id:'status',title:'Status and guarded approvals',matches:id=>id.startsWith('voice.')},
  {id:'workspace',title:'Workspace panels',matches:id=>id.startsWith('drawer.show:')},
  {id:'projects',title:'Current Projects by name',matches:id=>id.startsWith('project.focus:')},
  {id:'sessions',title:'Current sessions by name',matches:id=>id.startsWith('session.focus:')},
  {id:'launch',title:'Start a session in a Project',matches:id=>id.startsWith('session.spawn:')},
  {id:'other',title:'Other current commands',matches:()=>true},
]

/**
 * Convert the live command registry into display-only groups.
 * The catch-all query entry is omitted because its literal `{text}` phrase is an
 * implementation hook; the fixed grammar is listed separately in Settings.
 */
export function registeredVoiceReference(commands:Command[]):VoiceReferenceGroup[]{
  const grouped=new Map<VoiceReferenceGroup['id'],VoiceReferenceCommand[]>()
  for(const command of commands){
    if(!command.voice||command.id==='voice.query')continue
    const phrases=[...new Set(command.voice.phrases.map(phrase=>phrase.trim()).filter(Boolean))]
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
