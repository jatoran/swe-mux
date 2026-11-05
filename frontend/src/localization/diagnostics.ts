import { locale, setLocalizationReporter } from './index.ts'

type Diagnostic={operation:string;locale:string;message_id?:string;reason?:string}
let started=false
let timer:ReturnType<typeof setTimeout>|undefined
const pending:Diagnostic[]=[]

/** Bounded, content-free diagnostics use the daemon's rotating, request-correlated log. */
export function startLocalizationDiagnostics():void {
  if(started)return
  started=true
  const flush=()=>{
    if(timer!==undefined)clearTimeout(timer)
    timer=undefined
    if(!pending.length)return
    const entries=pending.splice(0,20)
    void fetch('/api/diagnostics/localization',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({entries}),keepalive:true,
    }).catch(()=>{ /* A disconnected daemon must never prevent a locale change. */ })
  }
  const report=(entry:Diagnostic)=>{
    if(entry.operation==='message_fallback')console.warn('localization',entry)
    if(pending.length<20)pending.push(entry)
    if(timer===undefined)timer=setTimeout(flush,500)
  }
  setLocalizationReporter(report)
  report({operation:'locale_loaded',locale:locale.peek()})
  window.addEventListener('pagehide',flush)
}
