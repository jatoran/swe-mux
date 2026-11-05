import { rich, t } from './localization/index.ts'
import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import type { ProviderAccountsStatus } from './ProviderAccounts'

export function SetupAccounts({enabled}:{enabled:string[]}) {
  const [accounts,setAccounts]=useState<ProviderAccountsStatus|null>(null)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState('')
  const reload=()=>api<ProviderAccountsStatus>('GET','/api/provider-accounts',undefined,{timeoutMs:15000}).then(value=>{setAccounts(value);setError('')})
  const running=Object.values(accounts?.login||{}).some(login=>login?.state==='running')
  useEffect(()=>{void reload().catch(cause=>setError(cause.message))},[])
  useEffect(()=>{
    if(!running)return
    const timer=setInterval(()=>void reload().catch(cause=>setError(cause.message)),2500)
    return()=>clearInterval(timer)
  },[running])
  const action=async(provider:string,operation:'capture'|'login'|'login/dismiss')=>{
    setBusy(provider);setError('')
    try{setAccounts(await api<ProviderAccountsStatus>('POST',`/api/provider-accounts/${provider}/${operation}`,{},{timeoutMs:15000}));window.dispatchEvent(new Event('swe-mux:provider-accounts-changed'))}
    catch(cause){setError((cause as Error).message)}finally{setBusy('')}
  }
  return <section class="setup-accounts"><h3 data-heading-message="SetupAccounts.c6eb55dce3">{t("SetupAccounts.c6eb55dce3")}</h3><p class="setup-hint">{t("SetupAccounts.ad98ecfc3b")}</p>
    {accounts?.providers.filter(provider=>enabled.includes(provider)).map(provider=>{
      const current=accounts.current[provider],login=accounts.login?.[provider]
      const status=current?.state==='saved'?t("SetupAccounts.748a428faf"):current?.state==='external'?t("SetupAccounts.60b1283d87"):current?.state==='unreadable'?t("SetupAccounts.3b1ae9fbb2"):t("SetupAccounts.31e23ff084")
      return <div class="harness-setup-row" key={provider}><span><strong>{provider}</strong><small>{current?.email||current?.organization || ''} {status}</small>{login?.state==='running' && <small role="status">{t("SetupAccounts.92b56db48d")}</small>}{login?.state==='failed' && <small role="alert">{login.error || t("SetupAccounts.57b7106b34")}</small>}{current?.state==='signed_out'&&accounts.login_commands?.[provider] && <small>{rich("SetupAccounts.23d15ae18e", { value1: <code>{accounts.login_commands[provider]}</code> })}</small>}</span>
        {login?.state==='running' ? <button disabled={!!busy} onClick={()=>void action(provider,'login/dismiss')}>{t("SetupAccounts.9effa0b748")}</button> : current?.state==='external' ? <button disabled={!!busy} onClick={()=>void action(provider,'capture')}>{t("SetupAccounts.9f73f988bd")}</button> : current?.state!=='saved' && <button disabled={!!busy} onClick={()=>void action(provider,'login')}>{t("SetupAccounts.532b191d5a")}</button>}
      </div>
    })}
    <button disabled={!!busy} class="link" onClick={()=>void reload().catch(cause=>setError(cause.message))}>{t("SetupAccounts.d07501f278")}</button>
    {error && <p role="alert">{error}</p>}
  </section>
}
