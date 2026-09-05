import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import type { ProviderAccountsStatus } from './ProviderAccounts'

/** Uses the account manager's live identity and capture operation; never reads a secret. */
export function SetupAccounts({enabled}:{enabled:string[]}) {
  const [accounts,setAccounts]=useState<ProviderAccountsStatus|null>(null)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState('')
  const reload=()=>api<ProviderAccountsStatus>('GET','/api/provider-accounts',undefined,{timeoutMs:15000}).then(setAccounts)
  useEffect(()=>{void reload().catch(cause=>setError(cause.message))},[])
  const capture=async(provider:string)=>{
    setBusy(provider);setError('')
    try{setAccounts(await api<ProviderAccountsStatus>('POST',`/api/provider-accounts/${provider}/capture`,{}));window.dispatchEvent(new Event('swe-mux:provider-accounts-changed'))}
    catch(cause){setError((cause as Error).message)}finally{setBusy('')}
  }
  return <section class="setup-accounts"><h3>Use your existing login</h3>
    <p>Saving an account stores a credential snapshot locally for account usage, quota tracking, and switching. This is optional and does not provide the model API key used by automations.</p>
    {accounts?.providers.filter(provider=>enabled.includes(provider)).map(provider=>{
      const current=accounts.current[provider]
      return <div class="harness-setup-row" key={provider}><span><strong>{provider}</strong><small>{current?.email||current?.organization||''} {current?.state==='saved'?'Already saved':current?.state==='external'?'Already signed in':current?.state==='unreadable'?'Login could not be read':'Not signed in'}{current?.state==='signed_out'&&accounts.login_commands?.[provider]?` - run ${accounts.login_commands[provider]}`:''}</small></span>{current?.state==='external'&&<button disabled={!!busy} onClick={()=>void capture(provider)}>{busy===provider?'Saving…':'Save current login'}</button>}</div>
    })}
    {error&&<p role="alert">{error} <button onClick={()=>void reload().catch(cause=>setError(cause.message))}>Retry</button></p>}
  </section>
}
