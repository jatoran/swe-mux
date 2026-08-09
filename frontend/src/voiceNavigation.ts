import { terminalIds, type PaneLayout } from './layout.ts'
import type { Project, Session } from './types.ts'

export type VoiceSessionAddress={projectNumber:number;sessionNumber:number}

export type VoiceNavigationIndex={
  projects:Project[]
  projectNumberById:Map<string,number>
  sessionsByProject:Map<string,Session[]>
  sessionAddressById:Map<string,VoiceSessionAddress>
}

/**
 * Build the spoken navigation index from the same ordering the sidebar renders.
 * Pending optimistic rows are not addressable because their ids and placement are not final.
 */
export function buildVoiceNavigationIndex(
  projects:Project[],
  sessions:Session[],
  layoutFor:(project:Project)=>PaneLayout,
):VoiceNavigationIndex{
  const projectNumberById=new Map(projects.map((project,index)=>[project.id,index+1]))
  const sessionsByProject=new Map<string,Session[]>()
  const sessionAddressById=new Map<string,VoiceSessionAddress>()
  for(const [projectIndex,project] of projects.entries()){
    const children=sessions
      .filter(session=>session.project_id===project.id&&!session.pending)
      .sort((left,right)=>left.created_at-right.created_at||left.id.localeCompare(right.id))
    const byId=new Map(children.map(session=>[session.id,session]))
    const paneOrder=terminalIds(layoutFor(project))
    const seen=new Set<string>()
    const ordered:Session[]=[]
    for(const id of paneOrder){
      const session=byId.get(id)
      if(session&&!seen.has(id)){seen.add(id);ordered.push(session)}
    }
    for(const session of children)if(!seen.has(session.id))ordered.push(session)
    sessionsByProject.set(project.id,ordered)
    ordered.forEach((session,sessionIndex)=>sessionAddressById.set(session.id,{
      projectNumber:projectIndex+1,
      sessionNumber:sessionIndex+1,
    }))
  }
  return{projects:[...projects],projectNumberById,sessionsByProject,sessionAddressById}
}

export function projectAtVoiceNumber(index:VoiceNavigationIndex,number:number):Project|null{
  return Number.isInteger(number)&&number>0?index.projects[number-1]||null:null
}

export function sessionAtVoiceNumber(
  index:VoiceNavigationIndex,projectId:string,number:number,
):Session|null{
  return Number.isInteger(number)&&number>0?index.sessionsByProject.get(projectId)?.[number-1]||null:null
}

/** Resolve a neighboring session from the canonical rendered order without wrapping. */
export function adjacentVoiceSession(
  index:VoiceNavigationIndex,
  projectId:string,
  sessionId:string,
  direction:-1|1,
):Session|null{
  const ordered=index.sessionsByProject.get(projectId)||[]
  const current=ordered.findIndex(session=>session.id===sessionId)
  return current<0?null:ordered[current+direction]||null
}
