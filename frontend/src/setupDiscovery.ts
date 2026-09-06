import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api.ts'

export type SetupProjectCandidate = {root:string;name:string;last_activity:number;sessions:number;harnesses:string[];available:boolean}
type Result = {items:SetupProjectCandidate[];limited:boolean}
type Scan = {state:'loading'|'ready'|'error';result?:Result;error?:string}
export type ProjectDiscovery = {items:SetupProjectCandidate[];scanning:boolean;limited:boolean;errors:string[];retry:()=>void}

/** Daemon paths, not the browser's OS: POSIX is case-sensitive, Windows is not. */
export const setupPathKey=(path:string)=>{
  const normalized=path.replaceAll('\\','/').replace(/\/+$/,'')
  return normalized.startsWith('/')&&!normalized.startsWith('//')?normalized:normalized.toLowerCase()
}

export function mergeSetupCandidates(results:SetupProjectCandidate[][]):SetupProjectCandidate[] {
  const found=new Map<string,SetupProjectCandidate>()
  for(const items of results)for(const item of items){
    const key=setupPathKey(item.root),previous=found.get(key)
    found.set(key,previous?{...item,last_activity:Math.max(item.last_activity,previous.last_activity),sessions:item.sessions+previous.sessions,harnesses:[...new Set([...previous.harnesses,...item.harnesses])]}:item)
  }
  return [...found.values()].sort((a,b)=>b.last_activity-a.last_activity||a.root.localeCompare(b.root))
}

/** Starts on selection, survives page changes, and publishes each harness independently. */
export function useProjectDiscovery(harnesses:string[]):ProjectDiscovery {
  const [scans,setScans]=useState<Record<string,Scan>>({})
  const active=useRef(new Map<string,AbortController>())
  const cache=useRef(new Map<string,Result>())
  const [retryToken,retry]=useState(0)
  const selected=[...new Set(harnesses)].sort()
  const key=selected.join(',')
  useEffect(()=>{
    const selectedSet=new Set(selected)
    for(const [name,controller] of active.current)if(!selectedSet.has(name)){controller.abort();active.current.delete(name)}
    setScans(current=>Object.fromEntries(selected.map(name=>[name,cache.current.has(name)?{state:'ready',result:cache.current.get(name)}:current[name]||{state:'loading'}])))
    const timer=setTimeout(()=>{
      for(const name of selected){
        if(cache.current.has(name)||active.current.has(name))continue
        const controller=new AbortController()
        active.current.set(name,controller)
        setScans(current=>({...current,[name]:{state:'loading'}}))
        void api<Result>('GET',`/api/onboarding/projects?harnesses=${encodeURIComponent(name)}`,undefined,{signal:controller.signal,timeoutMs:20000})
          .then(result=>{
            if(controller.signal.aborted)return
            cache.current.set(name,result)
            setScans(current=>({...current,[name]:{state:'ready',result}}))
          }).catch(cause=>{
            if(controller.signal.aborted)return
            setScans(current=>({...current,[name]:{state:'error',error:`${name}: ${(cause as Error).message}`}}))
          }).finally(()=>{if(active.current.get(name)===controller)active.current.delete(name)})
      }
    },200)
    return()=>clearTimeout(timer)
  },[key,retryToken])
  useEffect(()=>()=>{for(const controller of active.current.values())controller.abort();active.current.clear()},[])
  const visible=selected.map(name=>scans[name])
  return {
    items:mergeSetupCandidates(visible.flatMap(scan=>scan?.result?[scan.result.items]:[])),
    scanning:visible.some(scan=>!scan||scan.state==='loading'),
    limited:visible.some(scan=>scan?.result?.limited),
    errors:visible.flatMap(scan=>scan?.error?[scan.error]:[]),
    retry:()=>retry(value=>value+1),
  }
}
