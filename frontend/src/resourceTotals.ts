export type ResourceProcess={cpu_pct:number;memory_bytes:number;exited_at?:number;command?:string;memory_unique_bytes?:number|null}
export type ResourceBucket={processes:number;cpu_pct:number;memory_bytes:number}
/**
 * `memory_bytes` is RSS, i.e. the working set, which counts every shared page once per
 * process that maps it. Summed over a process tree it reads roughly 40% above the memory
 * ending those processes would actually return. `memory_unique_bytes` (USS) is that real
 * figure, but it costs ~200x an RSS read to sample, so the daemon only fills it in when a
 * view explicitly asks (`?unique_memory=1`) and it is absent from background polls.
 */
export type UniqueMemory={memory_unique_bytes?:number|null}
export type ResourceSnapshot={
  sessions:Array<{session_id:string;project_id?:string;processes:ResourceProcess[]}>
  totals?:ResourceBucket&UniqueMemory;daemon?:ResourceBucket&UniqueMemory&{pid:number}
}
export type ResourceSession={id:string;project_id:string}
export type ResourceProject={id:string;name:string;position:number}
export type ProjectResourceTotal={project_id:string;label:string;processes:number;cpu_pct:number;memory_bytes:number;memory_unique_bytes:number|null}

export function combinedResourceTotals(snapshot:ResourceSnapshot|null):ResourceBucket&UniqueMemory {
  if(!snapshot)return {processes:0,cpu_pct:0,memory_bytes:0}
  const session=snapshot.totals||{
    processes:snapshot.sessions.flatMap(group=>group.processes||[]).filter(process=>!process.exited_at).length,
    cpu_pct:snapshot.sessions.flatMap(group=>group.processes||[]).filter(process=>!process.exited_at).reduce((sum,process)=>sum+(Number(process.cpu_pct)||0),0),
    memory_bytes:snapshot.sessions.flatMap(group=>group.processes||[]).filter(process=>!process.exited_at).reduce((sum,process)=>sum+(Number(process.memory_bytes)||0),0),
  }
  const daemon=snapshot.daemon||{processes:0,cpu_pct:0,memory_bytes:0}
  // Unique memory is only meaningful when every contributing side reported it; a partial
  // sum would silently understate the total rather than admit it is unavailable.
  const sessionUnique=(session as UniqueMemory).memory_unique_bytes
  const daemonUnique=(daemon as UniqueMemory).memory_unique_bytes
  const unique=typeof sessionUnique==='number'&&typeof daemonUnique==='number'
    ?sessionUnique+daemonUnique
    :null
  return {
    processes:session.processes+daemon.processes,
    cpu_pct:Math.round((session.cpu_pct+daemon.cpu_pct)*10)/10,
    memory_bytes:session.memory_bytes+daemon.memory_bytes,
    memory_unique_bytes:unique,
  }
}

export function projectResourceTotals(snapshot:ResourceSnapshot|null,sessions:ResourceSession[],projects:ResourceProject[]):ProjectResourceTotal[] {
  const totals=new Map<string,ProjectResourceTotal>()
  const sessionsById=new Map(sessions.map(session=>[session.id,session]))
  const projectsById=new Map(projects.map(project=>[project.id,project]))
  for(const group of snapshot?.sessions||[]){
    const projectId=group.project_id||sessionsById.get(group.session_id)?.project_id||'unassigned'
    const processes=(group.processes||[]).filter(process=>!process.exited_at)
    if(!processes.length)continue
    const current=totals.get(projectId)||{project_id:projectId,label:projectsById.get(projectId)?.name||'Unassigned',processes:0,cpu_pct:0,memory_bytes:0,memory_unique_bytes:0}
    current.processes+=processes.length
    current.cpu_pct+=processes.reduce((sum,process)=>sum+(Number(process.cpu_pct)||0),0)
    current.memory_bytes+=processes.reduce((sum,process)=>sum+(Number(process.memory_bytes)||0),0)
    // One process missing its unique sample poisons the project total, because a
    // partial sum would read as a real (but too small) figure rather than "not sampled".
    current.memory_unique_bytes=current.memory_unique_bytes===null||processes.some(process=>typeof process.memory_unique_bytes!=='number')
      ?null
      :current.memory_unique_bytes+processes.reduce((sum,process)=>sum+(Number(process.memory_unique_bytes)||0),0)
    totals.set(projectId,current)
  }
  const position=new Map(projects.map(project=>[project.id,project.position]))
  return [...totals.values()]
    .map(item=>({...item,cpu_pct:Math.round(item.cpu_pct*10)/10}))
    .sort((left,right)=>(position.get(left.project_id)??Number.MAX_SAFE_INTEGER)-(position.get(right.project_id)??Number.MAX_SAFE_INTEGER)||left.label.localeCompare(right.label))
}
