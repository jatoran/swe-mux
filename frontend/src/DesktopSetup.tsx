import { useEffect, useState } from 'preact/hooks'
import { api } from './api'

export type DesktopIntegration={supported:boolean;shortcuts?:{slots:Record<string,{present:boolean;path:string}>};startup_enabled?:boolean;shell?:{importable:boolean;missing:string[];install_kind:string;reinstall_command:string}}
const SLOTS=[{id:'start-menu',label:'Start Menu entry'},{id:'desktop',label:'Desktop shortcut'},{id:'startup',label:'Start at sign-in, hidden in the tray'}]
export function DesktopSetup({onContinue}:{onContinue:(done:boolean)=>Promise<void>}) {
  const [status,setStatus]=useState<DesktopIntegration|null>(null)
  const [selected,setSelected]=useState<string[]>(['start-menu'])
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [message,setMessage]=useState('')
  const reload=()=>api<DesktopIntegration>('GET','/api/desktop/integration').then(value=>{setStatus(value);return value})
  useEffect(()=>{void reload().catch(cause=>setError(cause.message))},[])
  const present=(id:string)=>!!status?.shortcuts?.slots[id]?.present||(id==='startup'&&!!status?.startup_enabled)
  const install=async()=>{
    setBusy(true);setError('')
    try{
      const slots=selected.filter(id=>!present(id))
      if(slots.length){
        await api('POST','/api/desktop/integration/shortcuts',{slots})
        const next=await reload()
        if(slots.some(id=>!next.shortcuts?.slots[id]?.present))throw new Error('Some shortcuts could not be created. Check the reported locations and retry.')
      }
      setMessage('Desktop integration saved. Launch swe-mux from the Start Menu, or run swe-mux. Closing the window leaves sessions running in the tray.')
    }catch(cause){setError((cause as Error).message)}finally{setBusy(false)}
  }
  return <section><h2>Desktop and tray</h2><p>The Windows desktop app is included with a normal installation. Choose how you want to reach it and whether it starts at sign-in.</p>
    {status?.supported===false?<p>This platform uses the browser interface. No Windows shortcuts are needed.</p>:status&&<>
      {SLOTS.map(slot=><label class="harness-setup-row check" key={slot.id}><span><strong>{slot.label}</strong><small>{present(slot.id)?'Already configured':'Optional'}</small></span><input type="checkbox" disabled={busy||present(slot.id)} checked={present(slot.id)||selected.includes(slot.id)} onChange={event=>setSelected(current=>event.currentTarget.checked?[...current,slot.id]:current.filter(id=>id!==slot.id))}/></label>)}
      {status.shell&&!status.shell.importable&&<p role="alert">Desktop dependencies are missing. {status.shell.reinstall_command}</p>}
      <button class="primary" disabled={busy||!status.shell?.importable} onClick={()=>void install()}>Create selected shortcuts</button>
    </>}
    {message&&<p role="status">{message}</p>}{error&&<p role="alert">{error} <button onClick={()=>void reload().catch(cause=>setError(cause.message))}>Retry</button></p>}
    <footer><button disabled={busy} onClick={()=>void onContinue(status?.supported===false||present('start-menu')).catch(cause=>setError(cause.message))}>Continue</button></footer>
  </section>
}
