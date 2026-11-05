import { rich, t } from './localization/index.ts'
import { useEffect, useState } from 'preact/hooks'
import { api } from './api'

export type DesktopIntegration={supported:boolean;shortcuts?:{slots:Record<string,{present:boolean;path:string}>};startup_enabled?:boolean;shell?:{importable:boolean;missing:string[];install_kind:string;reinstall_command:string}}
const SLOTS=[{id:'start-menu',get label() { return t("DesktopSetup.59e3ebeece") }},{id:'desktop',get label() { return t("DesktopSetup.3ba33ecebd") }},{id:'startup',get label() { return t("DesktopSetup.608d04153a") }}]
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
        if(slots.some(id=>!next.shortcuts?.slots[id]?.present))throw new Error(t("DesktopSetup.1066630195"))
      }
      setMessage(t("DesktopSetup.33e85210c5"))
    }catch(cause){setError((cause as Error).message)}finally{setBusy(false)}
  }
  return <section><h2 data-heading-message="DesktopSetup.6a495c94b7">{t("DesktopSetup.6a495c94b7")}</h2><p>{t("DesktopSetup.8c6829053b")}</p>
    {status?.supported===false ? <p>{t("DesktopSetup.65ed0ee011")}</p> : status && <>
      {SLOTS.map(slot=><label class="harness-setup-row check" key={slot.id}><span><strong>{slot.label}</strong><small>{present(slot.id) ? t("DesktopSetup.a96c672479") : t("DesktopSetup.59be71333c")}</small></span><input type="checkbox" disabled={busy||present(slot.id)} checked={present(slot.id)||selected.includes(slot.id)} onChange={event=>setSelected(current=>event.currentTarget.checked?[...current,slot.id]:current.filter(id=>id!==slot.id))}/></label>)}
      {status.shell&&!status.shell.importable && <p role="alert">{rich("DesktopSetup.b9f395da43", { reinstall_command: status.shell.reinstall_command })}</p>}
      <button class="primary" disabled={busy||!status.shell?.importable} onClick={()=>void install()}>{t("DesktopSetup.f1deedaaf1")}</button>
    </>}
    {message && <p role="status">{message}</p>}{error && <p role="alert">{error} <button onClick={()=>void reload().catch(cause=>setError(cause.message))}>{t("DesktopSetup.942087cc2d")}</button></p>}
    <footer><button disabled={busy} onClick={()=>void onContinue(status?.supported===false||present('start-menu')).catch(cause=>setError(cause.message))}>{t("DesktopSetup.31fbef1625")}</button></footer>
  </section>
}
