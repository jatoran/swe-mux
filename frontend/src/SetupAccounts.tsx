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
  return <section class="setup-accounts"><h3>Agent accounts</h3><p class="setup-hint">Use an existing login, or sign in now. Saving keeps a local snapshot for account switching.</p>
    {accounts?.providers.filter(provider=>enabled.includes(provider)).map(provider=>{
      const current=accounts.current[provider],login=accounts.login?.[provider]
      const status=current?.state==='saved'?'Already saved':current?.state==='external'?'Already signed in':current?.state==='unreadable'?'Login could not be read':'Sign-in needed'
      return <div class="harness-setup-row" key={provider}><span><strong>{provider}</strong><small>{current?.email||current?.organization||''} {status}</small>{login?.state==='running'&&<small role="status">Finish sign-in in the browser on this computer.</small>}{login?.state==='failed'&&<small role="alert">{login.error||'Sign-in failed. Try again.'}</small>}{current?.state==='signed_out'&&accounts.login_commands?.[provider]&&<small>Or run <code>{accounts.login_commands[provider]}</code> in a terminal.</small>}</span>
        {login?.state==='running'?<button disabled={!!busy} onClick={()=>void action(provider,'login/dismiss')}>Cancel sign-in</button>:current?.state==='external'?<button disabled={!!busy} onClick={()=>void action(provider,'capture')}>Save current login</button>:current?.state!=='saved'&&<button disabled={!!busy} onClick={()=>void action(provider,'login')}>Sign in and save</button>}
      </div>
    })}
    <button disabled={!!busy} class="link" onClick={()=>void reload().catch(cause=>setError(cause.message))}>Check login again</button>
    {error&&<p role="alert">{error}</p>}
  </section>
}
