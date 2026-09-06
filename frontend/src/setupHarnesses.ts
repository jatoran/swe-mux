import { useEffect, useState } from 'preact/hooks'
import { api } from './api.ts'
import { installHarnessRegistry, type HarnessRegistryPayload } from './harnessRegistry.ts'

export function useSetupHarnesses() {
  const [registry,setRegistry]=useState<HarnessRegistryPayload|null>(null)
  const [error,setError]=useState('')
  const [loading,setLoading]=useState(true)
  const [attempt,setAttempt]=useState(0)
  useEffect(()=>{
    const controller=new AbortController()
    setLoading(true);setError('')
    void (async()=>{
      if(attempt)await api('POST','/api/diagnostics/prerequisites/refresh',{}, {signal:controller.signal,timeoutMs:15000})
      const result=await api<HarnessRegistryPayload>('GET','/api/harnesses',undefined,{signal:controller.signal,timeoutMs:15000})
      if(controller.signal.aborted)return
      installHarnessRegistry(result);setRegistry(result)
    })().catch(cause=>{if(!controller.signal.aborted)setError((cause as Error).message)})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false)})
    return()=>controller.abort()
  },[attempt])
  return {registry,error,loading,retry:()=>setAttempt(value=>value+1)}
}
